# tinymacho

A hand-assembled, runnable Mach-O executable for Apple Silicon macOS —
built byte-by-byte from Python, without invoking a compiler or linker.

The only "external tool" used on the output is `codesign`, which signs
(rather than compiles) the binary. On modern arm64 macOS this step is
non-negotiable: the kernel refuses to execute unsigned binaries.

## Rationale

The goal was to answer a simple question: *what is the smallest file
I can write, by hand, that the OS will accept as a runnable binary
and successfully execute?*

No compiler. No linker. No assembler. Just `struct.pack` writing the
exact bytes of the Mach-O format, plus `codesign` to satisfy AMFI.

The binary does nothing but call `exit(0)`.

## Files

- `build_minimal.py` — the generator. Emits `tinymacho` byte-by-byte.
- `tinymacho` — the resulting 34,672-byte arm64 Mach-O executable.

## Running

```sh
python3 build_minimal.py
tinymacho; echo $?     # prints 0
```

## What the binary contains

The "useful" part of the file is only 364 bytes:

| Region                  | Size  | Notes                                        |
|-------------------------|------:|----------------------------------------------|
| Mach-O header           |    32 | `MH_MAGIC_64`, arm64, `MH_EXECUTE`, 7 cmds   |
| `LC_SEGMENT_64` PAGEZERO|    72 | 4 GB unmapped guard region                   |
| `LC_SEGMENT_64` TEXT    |    72 | Contains the header + code                   |
| `LC_SEGMENT_64` LINKEDIT|    72 | Holds the code signature                     |
| `LC_LOAD_DYLINKER`      |    24 | Points at `/usr/lib/dyld`                    |
| `LC_MAIN`               |    24 | Entry point = file offset of code            |
| `LC_BUILD_VERSION`      |    24 | Platform = macOS 11.0                        |
| `LC_CODE_SIGNATURE`     |    16 | Points into __LINKEDIT                       |
| Code (3 instructions)   |    12 | `mov x16,#1 ; mov x0,#0 ; svc #0x80`         |

The three instructions follow Darwin's arm64 syscall ABI: syscall number
in `x16`, arguments in `x0..x5`, trap via `svc #0x80`. Syscall 1 is
`exit`. The canonical list lives in XNU's
[`bsd/kern/syscalls.master`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/syscalls.master).

Everything else is padding and the ad-hoc code signature blob that
`codesign` writes after the fact.

## What I learned making it actually run

Getting a hand-crafted Mach-O to pass `exec()` on a current arm64 Mac
(Darwin 25.4 / macOS 26, with SIP and Gatekeeper both enabled) is much
harder than the equivalent exercise on Linux. Several dead ends:

### 1. Unsigned binaries are killed outright

A valid, well-formed Mach-O runs for ~0 instructions before AMFI
(`AppleMobileFileIntegrity`) sends `SIGKILL`. Every binary on arm64
must carry at least an ad-hoc code signature. `codesign -s - <path>`
creates one.

Normally you never notice this: since Xcode 14, Apple's linker (`ld`)
applies an ad-hoc signature by default on arm64 as the last step of
linking (see `-adhoc_codesign` in `man ld`). A `clang hello.c` binary
"just runs" because `ld` already signed it. Here we skip the linker
entirely, so we have to invoke `codesign` ourselves — it's not extra
ceremony, it's the step the linker would have done.

### 2. `LC_UNIXTHREAD` is no longer allowed for main executables

The textbook "minimal Mach-O" uses `LC_UNIXTHREAD`: you put the initial
register state (including `pc`) directly in the load command, and the
kernel jumps to it without involving dyld. On modern arm64 macOS this
is rejected by `AppleSystemPolicy`:

```
kernel: ASP: Security policy would not allow process: …/tiny
```

Main executables must use `LC_MAIN`, which implies going through
`/usr/lib/dyld`. That in turn requires `LC_LOAD_DYLINKER`. Adding those
two load commands fixed the ASP rejection.

### 3. `__TEXT` must be page-aligned in the *file*, not just in VM

With a ~364-byte `__TEXT` `filesize` (actual content) but a 16 KB
`vmsize`, `amfid` rejected the binary as "not valid … adhoc signed or
signed by an unknown certificate chain." Misleading error — the real
cause wasn't the signature at all.

The fix was padding the `__TEXT` segment's *file* contents out to a
full 16 KB arm64 page so `filesize == vmsize`. This is where the bulk
of the binary's size comes from: the page size, not the payload.

### 4. `codesign` needs a valid placeholder signature blob

Calling `codesign` on a Mach-O with `LC_CODE_SIGNATURE.datasize == 0`
fails with "invalid or unsupported format for signature" — it tries to
parse whatever's at `dataoff` before rewriting it. Writing a minimal
12-byte empty `SuperBlob` (`magic=0xfade0cc0, length=12, count=0`) at
that offset makes `codesign` happy; it then replaces the placeholder
with a real CodeDirectory.

### 5. Why the final size is ~34 KB, not ~400 bytes

- `__TEXT` must be a full page  → 16,384 bytes.
- `codesign` pads `__LINKEDIT` to another page for its signature blob
  → another ~16 KB.
- Net: ~34 KB is roughly the floor for a hand-built, signed arm64
  Mach-O on this platform. You cannot get meaningfully smaller without
  disabling SIP or using a developer-signed identity that allows
  different layout rules.

### The useful takeaway

On arm64 macOS, "smallest runnable binary" is bounded not by the
Mach-O format but by two platform policies:

1. **Code signing is mandatory.** No signature → `SIGKILL`.
2. **Segments must be page-sized in the file.** Even if the code is
   12 bytes, the __TEXT segment on disk must be 16 KB.

Everything else — the load commands, the dyld hand-off, the empty
signature blob placeholder — follows from trying to satisfy AMFI and
ASP with the minimum number of structural features.
