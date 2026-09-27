"""Endpoint-local, user-scoped Windows DPAPI storage for shared HMAC keys."""
import base64
import csv
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import subprocess


class Blob(ctypes.Structure):
    _fields_ = [("Length", wintypes.DWORD), ("Data", ctypes.POINTER(ctypes.c_ubyte))]


def Dpapi(Data, Decrypt=False):
    if os.name != "nt":
        raise OSError("production key storage requires Windows user DPAPI")
    Crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    Kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    Crypt.CryptProtectData.argtypes = (ctypes.POINTER(Blob), wintypes.LPCWSTR,
                                     ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                     wintypes.DWORD, ctypes.POINTER(Blob))
    Crypt.CryptUnprotectData.argtypes = (ctypes.POINTER(Blob), ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(Blob))
    Kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    Kernel.LocalFree.restype = ctypes.c_void_p
    Source = ctypes.create_string_buffer(Data)
    Input = Blob(len(Data), ctypes.cast(Source, ctypes.POINTER(ctypes.c_ubyte)))
    Output = Blob()
    Function = Crypt.CryptUnprotectData if Decrypt else Crypt.CryptProtectData
    Arguments = (ctypes.byref(Input), None, None, None, None, 1, ctypes.byref(Output))
    if not Function(*Arguments):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(Output.Data, Output.Length)
    finally:
        Kernel.LocalFree(Output.Data)


def PrivateKeyPath(File):
    Root = (Path.home() / ".codex" / "agent-coordinator" / "secrets").resolve()
    Result = Path(File).resolve()
    if not Result.is_relative_to(Root) or Result.suffix != ".dpapi":
        raise ValueError("key file must be in the endpoint user's private secrets directory")
    return Result


def PrivateConfigPath(File):
    Root = (Path.home() / ".codex" / "agent-coordinator").resolve()
    Result = Path(File).resolve()
    if not Result.is_relative_to(Root) or Result.suffix != ".json":
        raise ValueError("daemon config must be in the endpoint user's private coordinator directory")
    Restrict(Root, Directory=True)
    Restrict(Result)
    return Result


def Key(Value):
    if not isinstance(Value, str) or len(Value) != 64:
        raise ValueError("HMAC key must be 32 random bytes in hex")
    try:
        return bytes.fromhex(Value)
    except ValueError as Error:
        raise ValueError("invalid HMAC key") from Error


def Restrict(File, Directory=False):
    """Remove inherited profile ACLs, including Codex's sandbox read ACE."""
    if os.name != "nt":
        raise OSError("Windows ACL required")
    Sid = next(csv.reader([subprocess.check_output(
        ["whoami", "/user", "/fo", "csv", "/nh"], text=True).strip()]))[1]
    PathValue = str(File)
    subprocess.run(["icacls", PathValue, "/inheritance:r"],
                   check=True, capture_output=True)
    Suffix = ":(OI)(CI)F" if Directory else ":F"
    subprocess.run(["icacls", PathValue, "/grant:r", "*" + Sid + Suffix,
                    "*S-1-5-18" + Suffix, "*S-1-5-32-544" + Suffix],
                   check=True, capture_output=True)


def StoreKey(File, Value, Rotate=False):
    File = PrivateKeyPath(File)
    Raw = Key(Value)
    File.parent.mkdir(parents=True, exist_ok=True)
    Restrict(File.parent.parent, Directory=True)
    Restrict(File.parent, Directory=True)
    if File.exists() and not Rotate:
        raise FileExistsError("key already provisioned; explicit rotation required")
    Encoded = base64.b64encode(Dpapi(Raw)).decode("ascii")
    Temporary = File.with_name(File.name + ".new-" + secrets.token_hex(8))
    try:
        with Temporary.open("x", encoding="ascii") as Output:
            json.dump({"Version": 1, "Scope": "WindowsCurrentUser", "Ciphertext": Encoded}, Output)
        if File.exists() and not Rotate:
            raise FileExistsError("key already provisioned; explicit rotation required")
        Temporary.replace(File)
        Restrict(File)
    finally:
        Temporary.unlink(missing_ok=True)


def LoadKey(File):
    File = PrivateKeyPath(File)
    if not File.exists():
        raise FileNotFoundError(File)
    Restrict(File.parent.parent, Directory=True)
    Restrict(File.parent, Directory=True)
    Restrict(File)
    if File.stat().st_size > 4096:
        raise ValueError("key file too large")
    Item = json.loads(File.read_text(encoding="ascii"))
    if type(Item) is not dict or set(Item) != {"Version", "Scope", "Ciphertext"} or \
            Item["Version"] != 1 or Item["Scope"] != "WindowsCurrentUser":
        raise ValueError("unsupported key storage format")
    Raw = Dpapi(base64.b64decode(Item["Ciphertext"], validate=True), Decrypt=True)
    if len(Raw) != 32:
        raise ValueError("invalid protected key length")
    return Raw.hex()


def RevokeKey(File):
    PrivateKeyPath(File).unlink()
