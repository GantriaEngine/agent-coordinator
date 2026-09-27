"""Strict declarative workflows and locally installed capability catalogs."""
import hashlib
import json
import re
import time


def Exact(Value, Keys):
    if not isinstance(Value, dict) or set(Value) != set(Keys):
        raise ValueError("unknown or missing fields")


def Symbol(Value):
    if not isinstance(Value, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9.-]{0,95}", Value):
        raise ValueError("invalid symbolic identity")


def CapabilityId(Value):
    Symbol(Value)
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9.-]*\.v[1-9][0-9]{0,3}", Value):
        raise ValueError("capability must have an independent version")


class Workflow:
    def __init__(self, Value):
        Exact(Value, ("SchemaId", "SchemaVersion", "Roles", "States", "Transitions",
                      "Parameters", "RegistrationTimeout", "ExecutionTimeout", "Abort"))
        Symbol(Value["SchemaId"])
        if Value["SchemaVersion"] != 1 or type(Value["SchemaVersion"]) is not int:
            raise ValueError("unsupported workflow format version")
        for Key in ("RegistrationTimeout", "ExecutionTimeout"):
            if type(Value[Key]) is not int or not 1 <= Value[Key] <= 600:
                raise ValueError("timeout outside 1..600 seconds")
        if Value["Abort"] != "local-cleanup":
            raise ValueError("unsupported abort behavior")
        Roles = Value["Roles"]
        if not isinstance(Roles, dict) or not 1 <= len(Roles) <= 16:
            raise ValueError("role bound exceeded")
        for Role, Required in Roles.items():
            Symbol(Role)
            if not isinstance(Required, list) or not 1 <= len(Required) <= 16 or len(set(Required)) != len(Required):
                raise ValueError("invalid capability requirements")
            for Capability in Required:
                CapabilityId(Capability)
        Parameters = Value["Parameters"]
        if not isinstance(Parameters, dict) or len(Parameters) > 16:
            raise ValueError("invalid parameter definitions")
        for Name, Bounds in Parameters.items():
            Symbol(Name)
            Exact(Bounds, ("Minimum", "Maximum"))
            if any(type(Bounds[Key]) is not int for Key in Bounds) or not 0 <= Bounds["Minimum"] <= Bounds["Maximum"] <= 1000000:
                raise ValueError("invalid numeric bounds")
        States = Value["States"]
        if not isinstance(States, list) or not 2 <= len(States) <= 65 or len(set(States)) != len(States):
            raise ValueError("invalid state list")
        for State in States:
            Symbol(State)
        Steps = Value["Transitions"]
        if not isinstance(Steps, list) or len(Steps) != len(States) - 1:
            raise ValueError("v1 requires a bounded ordered workflow")
        for Index, Step in enumerate(Steps):
            Exact(Step, ("From", "To", "Role", "Capability", "Response", "Parameters", "Timeout"))
            if (Step["From"] != States[Index] or Step["To"] != States[Index + 1] or
                    Step["Role"] not in Roles or Step["Capability"] not in Roles[Step["Role"]] or
                    Step["Response"] not in ("LIVE", "RESULT") or
                    not isinstance(Step["Parameters"], list) or
                    not set(Step["Parameters"]) <= set(Parameters) or
                    type(Step["Timeout"]) is not int or not 1 <= Step["Timeout"] <= 90):
                raise ValueError("unauthorized transition or capability")
        self.Value = json.loads(json.dumps(Value))
        self.Hash = hashlib.sha256(json.dumps(Value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def Parameters(self, Values):
        Exact(Values, self.Value["Parameters"])
        for Name, Number in Values.items():
            Bounds = self.Value["Parameters"][Name]
            if type(Number) is not int or not Bounds["Minimum"] <= Number <= Bounds["Maximum"]:
                raise ValueError("parameter outside schema bounds")
        return dict(Values)

    def Registration(self, Role, Advertised):
        if (Role not in self.Value["Roles"] or not isinstance(Advertised, list) or
                len(Advertised) > 16 or len(set(Advertised)) != len(Advertised)):
            raise ValueError("invalid role/capability advertisement")
        for Capability in Advertised:
            CapabilityId(Capability)
        # v1 exposes exactly the selected role's approved capabilities.
        if set(Advertised) != set(self.Value["Roles"][Role]):
            raise ValueError("missing or unknown required capability")

    def Transition(self, Index, Role, Capability, From, To):
        if type(Index) is not int or not 0 <= Index < len(self.Value["Transitions"]):
            raise ValueError("unknown transition")
        Step = self.Value["Transitions"][Index]
        if (Role, Capability, From, To) != (Step["Role"], Step["Capability"], Step["From"], Step["To"]):
            raise ValueError("unauthorized transition")
        return Step


class Catalog:
    """Callables originate only from installed local adapter code, never JSON."""
    def __init__(self, Handlers, Cleanup):
        if not callable(Cleanup):
            raise ValueError("local cleanup is required")
        for Name, Handler in Handlers.items():
            CapabilityId(Name)
            if not callable(Handler):
                raise ValueError("capability must be installed locally")
        self.Handlers = dict(Handlers)
        self.Cleanup = Cleanup

    def Advertise(self, Workflow, Role):
        Required = Workflow.Value["Roles"].get(Role, [])
        if not Required or not set(Required) <= set(self.Handlers):
            raise ValueError("missing locally approved capability")
        return list(Required)

    def Invoke(self, Name, Advertised, Parameters, Deadline):
        if Name not in Advertised or Name not in self.Handlers:
            raise ValueError("capability not locally exposed")
        Context = Operation(Deadline)
        Context.Check()
        Result = self.Handlers[Name](dict(Parameters), Context)
        Context.Check()
        Exact(Result, ("Success", "Evidence"))
        if type(Result["Success"]) is not bool or not isinstance(Result["Evidence"], list) or len(Result["Evidence"]) > 32:
            raise ValueError("invalid result")
        for Item in Result["Evidence"]:
            Exact(Item, ("Path", "SHA256", "Bytes"))
            if (not isinstance(Item["Path"], str) or len(Item["Path"]) > 512 or
                    not isinstance(Item["SHA256"], str) or not re.fullmatch(r"[a-fA-F0-9]{64}", Item["SHA256"]) or
                    type(Item["Bytes"]) is not int or Item["Bytes"] < 0):
                raise ValueError("invalid evidence metadata")
        return Result


class Operation:
    def __init__(self, Deadline):
        self.Deadline = Deadline

    def Check(self):
        if time.monotonic() >= self.Deadline:
            raise TimeoutError("capability deadline expired")
