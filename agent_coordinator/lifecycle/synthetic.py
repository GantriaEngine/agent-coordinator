"""Harmless installed synthetic catalog for lifecycle qualification only."""
from ..workflow import Catalog


def Invoke(Parameters, Context):
    Context.Check()
    return {"Success": True, "Evidence": []}


def GetCatalog():
    return Catalog({"example.server-ready.v1": Invoke, "example.client-result.v1": Invoke}, lambda: None)
