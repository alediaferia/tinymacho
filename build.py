#!/usr/bin/env python3
"""Minimal Mach-O arm64 executable via dyld + LC_MAIN, byte-by-byte.

Modern arm64 macOS (SIP + Gatekeeper) rejects LC_UNIXTHREAD for main
binaries via AppleSystemPolicy — we must go through /usr/lib/dyld.

Output: tinymacho
"""
import struct, os, subprocess

# ---------- constants ----------
MH_MAGIC_64      = 0xfeedfacf
CPU_TYPE_ARM64   = 0x0100000c
MH_EXECUTE       = 2
MH_NOUNDEFS      = 0x1
MH_DYLDLINK      = 0x4
MH_TWOLEVEL      = 0x80
MH_PIE           = 0x200000

LC_REQ_DYLD      = 0x80000000
LC_SEGMENT_64    = 0x19
LC_LOAD_DYLINKER = 0xe
LC_MAIN          = 0x28 | LC_REQ_DYLD
LC_BUILD_VERSION = 0x32
LC_CODE_SIGNATURE= 0x1d

VM_R, VM_X = 1, 4
PAGE       = 0x4000

# ---------- code ----------
# To get the hex encoding of arm64 instructions, let an assembler do it:
#   echo 'mov x16,#1; mov x0,#0; svc #0x80' | as -arch arm64 -o /tmp/a.o
#   otool -t /tmp/a.o
CODE = struct.pack('<III',
    0xd2800030,   # mov x16, #1    (Darwin SYS_exit on arm64)
    0xd2800000,   # mov x0,  #0
    0xd4001001)   # svc #0x80

# ---------- load commands sizes ----------
def pad8(s): return s + b'\x00'*((-len(s)) & 7)
DYLINKER_PATH = pad8(b'/usr/lib/dyld\x00')
LC_DYLINKER_SIZE = 12 + len(DYLINKER_PATH)   # cmd+cmdsize+name.offset + string

SEG_SZ   = 72
MAIN_SZ  = 24
BV_SZ    = 24
CS_SZ    = 16

NCMDS      = 7       # PAGEZERO, TEXT, LINKEDIT, DYLINKER, MAIN, BUILD_VER, CODE_SIG
SIZEOFCMDS = SEG_SZ*3 + LC_DYLINKER_SIZE + MAIN_SZ + BV_SZ + CS_SZ
HDR_SZ     = 32
CMDS_END   = HDR_SZ + SIZEOFCMDS

# ---------- layout ----------
CODE_OFF   = CMDS_END
CODE_END   = CODE_OFF + len(CODE)
TEXT_FSZ   = PAGE            # pad __TEXT to full page (AMFI requirement on arm64)
LINK_OFF   = TEXT_FSZ
SIG_OFF    = LINK_OFF

TEXT_VM    = 0x100000000
LINK_VM    = TEXT_VM + PAGE
ENTRY_OFF  = CODE_OFF      # LC_MAIN uses file offset

# ---------- emit ----------
out = bytearray()

# Mach header
out += struct.pack('<IiiIIIII',
    MH_MAGIC_64, CPU_TYPE_ARM64, 0,
    MH_EXECUTE, NCMDS, SIZEOFCMDS,
    MH_NOUNDEFS|MH_DYLDLINK|MH_TWOLEVEL|MH_PIE, 0)

# LC_SEGMENT_64 __PAGEZERO
out += struct.pack('<II16sQQQQiiII',
    LC_SEGMENT_64, SEG_SZ,
    b'__PAGEZERO', 0, TEXT_VM, 0, 0, 0, 0, 0, 0)

# LC_SEGMENT_64 __TEXT
out += struct.pack('<II16sQQQQiiII',
    LC_SEGMENT_64, SEG_SZ,
    b'__TEXT',
    TEXT_VM, PAGE,
    0, TEXT_FSZ,
    VM_R|VM_X, VM_R|VM_X, 0, 0)

# LC_SEGMENT_64 __LINKEDIT
out += struct.pack('<II16sQQQQiiII',
    LC_SEGMENT_64, SEG_SZ,
    b'__LINKEDIT',
    LINK_VM, PAGE,
    LINK_OFF, 12,      # filesize placeholder (empty SuperBlob)
    VM_R, VM_R, 0, 0)

# LC_LOAD_DYLINKER
out += struct.pack('<III', LC_LOAD_DYLINKER, LC_DYLINKER_SIZE, 12)
out += DYLINKER_PATH

# LC_MAIN
out += struct.pack('<IIQQ', LC_MAIN, MAIN_SZ, ENTRY_OFF, 0)

# LC_BUILD_VERSION
def v(a,b,c=0): return (a<<16)|(b<<8)|c
out += struct.pack('<IIIIII', LC_BUILD_VERSION, BV_SZ, 1, v(11,0), v(11,0), 0)

# LC_CODE_SIGNATURE placeholder
out += struct.pack('<IIII', LC_CODE_SIGNATURE, CS_SZ, SIG_OFF, 12)

assert len(out) == CMDS_END, (len(out), CMDS_END)

# code
out += CODE
out += b'\x00' * (TEXT_FSZ - len(out))

# empty SuperBlob in __LINKEDIT
out += struct.pack('>III', 0xfade0cc0, 12, 0)

path = 'tinymacho'
with open(path, 'wb') as f:
    f.write(out)
os.chmod(path, 0o755)
print(f'unsigned size: {len(out)} bytes')

subprocess.run(['codesign', '-s', '-', '-f', path], check=True)
print(f'signed size:   {os.path.getsize(path)} bytes')
