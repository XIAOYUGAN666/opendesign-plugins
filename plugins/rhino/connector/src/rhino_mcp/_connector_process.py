"""Generated Windows software-process identity helper."""
import ctypes
import hashlib
import os
from ctypes import wintypes


def select_instance(records, executable_name):
    matches = [(created, pid) for name, pid, created in records if name.casefold() == executable_name.casefold() and pid > 0 and created > 0]
    if not matches:
        return None
    if len(matches) != 1:
        raise OSError("multipleSoftwareInstances")
    created, pid = matches[0]
    identity = hashlib.sha256(f"{executable_name.casefold()}:{pid}:{created}".encode("ascii")).hexdigest()
    return "si_" + identity


def running_instance(executable_name):
    if os.name != "nt":
        raise OSError("softwareProcessDetectionUnavailable")
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("softwareProcessDetectionUnavailable")
    records = []
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == executable_name.casefold():
                process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if not process:
                    raise OSError("softwareProcessIdentityUnavailable")
                try:
                    creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
                    if not kernel.GetProcessTimes(process, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                        raise OSError("softwareProcessIdentityUnavailable")
                    created = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                    records.append((entry.szExeFile, entry.th32ProcessID, created))
                finally:
                    kernel.CloseHandle(process)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise OSError("softwareProcessDetectionUnavailable")
    finally:
        kernel.CloseHandle(snapshot)
    return select_instance(records, executable_name)
