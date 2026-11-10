"""Windows' built-in spell checker (Windows 8+), via ctypes, to tell real English words
from the surnames and place names that make up much of the CMU dictionary.

    is_word("kitchen") -> True, is_word("pritchett") -> False, is_word(...) -> None if unavailable
"""

import ctypes
import threading
from ctypes import wintypes

_CLSID_FACTORY = "{7AB36653-1796-484B-BDFA-E74F1DB7C1DC}"
_IID_FACTORY = "{8E018A9D-2415-4677-BF08-794EA61F94BB}"
_local = threading.local()  # COM objects belong to the thread that made them


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


def _guid(text):
    g = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
    return g


def _method(obj, index, *argtypes):
    """Method number index of a COM object's vtable, as a callable taking obj first."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(obj):
    if obj:
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)


def _checker():
    if not hasattr(_local, "checker"):
        _local.checker = None
        try:
            ctypes.oledll.ole32.CoInitializeEx(None, 2)  # apartment-threaded; harmless if already done
        except OSError:
            pass
        try:
            factory = ctypes.c_void_p()
            ctypes.oledll.ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_FACTORY)), None, 1,
                                                 ctypes.byref(_guid(_IID_FACTORY)), ctypes.byref(factory))
            checker = ctypes.c_void_p()
            # ISpellCheckerFactory::CreateSpellChecker is vtable entry 5
            _method(factory, 5, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))(
                factory, "en-US", ctypes.byref(checker))
            _release(factory)
            _local.checker = checker
        except (OSError, AttributeError):
            _local.checker = None
    return _local.checker


def is_word(word):
    """True if Windows' en-US spell checker accepts the word as written, False if not, None if unavailable."""
    checker = _checker()
    if not checker:
        return None
    errors = ctypes.c_void_p()
    try:
        # ISpellChecker::Check is vtable entry 4; IEnumSpellingError::Next is entry 3
        _method(checker, 4, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))(checker, word, ctypes.byref(errors))
        error = ctypes.c_void_p()
        found = _method(errors, 3, ctypes.POINTER(ctypes.c_void_p))(errors, ctypes.byref(error))
        _release(error)
        return found != 0  # S_OK (0) means it found a spelling error; S_FALSE (1) means none
    except OSError:
        return None
    finally:
        _release(errors)
