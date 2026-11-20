# Review of PLAN.md

Reviewed: 2026-11-20, against `PLAN.md` as of that date. No code exists yet, so this is a design review.

## Summary

The structure of the plan is sound: the `core/` and `tui/` split, vendoring with a license whitelist, a shared formula description for checker and generator, and de-risking the platform in M0. The findings below fall into three groups.

- **A. Must fix before implementation.** Nine points where the design as written would produce wrong results, break on common compilers, or fail on real DBC files.
- **B. Decisions to make.** Nine points where the plan is silent or ambiguous. Each has a recommendation.
- **C. Corrections to the plan text.** Six small errors.

How each finding was checked:

| Mark | Meaning |
|---|---|
| **computed** | Reproduced with a Python script on this machine (Python 3.15.2). See the appendix. |
| **source** | Confirmed by reading the Textual 8.2.8 source or by a minimal zipapp test. |
| **reasoned** | Follows from the C standard, compiler documentation, or the DBC format. Not executed. |
| **to confirm** | Likely, but needs a test against the real tool (CANdb++). |

| ID | Finding | Plan section | Checked |
|---|---|---|---|
| A1 | FMA contraction is outside the verified model | §3.2, §4 | computed |
| A2 | Float-domain range check rejects the tool's own decoded values | §3.4 | computed |
| A3 | "Rohbereich" is not defined | §3.3 | reasoned |
| A4 | Enum-typed struct fields are unsafe | §4 | reasoned |
| A5 | Attribute names reused across object types | §2.4 | to confirm |
| A6 | Verbatim preservation conflicts with rename and delete | §2.3 | reasoned |
| A7 | `dbc2h.tcss` cannot be loaded from the `.pyz` | §2.1 | source |
| A8 | DBC file hash breaks `gen --check` | §4 | reasoned |
| A9 | Header claims C++ compatibility it does not have | §4 | reasoned |

---

## A. Must fix before implementation

### A1. FMA contraction is outside the verified model

**Finding.** §3.2 verifies the sequence "multiply, round, add, round". A C compiler may fuse `raw * factor + offset` into one fused multiply-add, which rounds only once. The result then differs from the model in the last bit.

- Clang contracts within an expression by default (`-ffp-contract=on`).
- GCC contracts by default outside ISO mode (`-std=gnu11`, the usual embedded setting). With `-std=c11` it is off.
- AArch64 and Cortex-M4F/M7 always have FMA. Baseline x86-64 does not, so an x86-64 CI never sees the effect.
- The guards in §4 (`__FAST_MATH__`, `FLT_EVAL_METHOD`) do not detect contraction.
- The test flags in §4 use `-std=c11`, which hides the effect in GCC.

**Evidence (computed).** Number of raw values that decode to a different `float` with FMA:

| Signal | Different results |
|---|---|
| 16 bit unsigned, factor 0.1, offset −40 | 1909 of 65536 |
| 16 bit unsigned, factor 0.01, offset −327.68 | 33447 of 65536 |
| 12 bit unsigned, factor 0.05, offset −102.4 | 2061 of 4096 |

**Recommendation.**

1. Make the guarantee independent of contraction. The shared formula description marks every multiply-add pair. The checker evaluates each such pair both ways and classifies by the worse result. Decode has one such pair, so the cost is one extra pass.
2. Emulate FMA with exact arithmetic and a single rounding. Do not go through `double` first: `Fraction → double → float` rounds twice and is wrong for sums. The appendix has a correct `Fraction → binary32` routine. For `double`, Python 3.13 has `math.fma`.
3. Where the factor is a power of two, the product is exact and both variants are identical. The checker can skip the second pass there.
4. Do not rely on pragmas. GCC ignores `#pragma STDC FP_CONTRACT` and warns about it, which fails under `-Wall -Werror`.
5. Add `#if defined(_M_FP_FAST)` as the MSVC counterpart to the `__FAST_MATH__` check.
6. In CI, compile and run the generated tests with `-ffp-contract=off` and `-ffp-contract=fast`, with `-std=c11` and `-std=gnu11`, on at least one FMA-capable target. The macOS arm64 runner qualifies.
7. State in the user documentation that `test_<prefix>.c` must be run with the production compiler and flags. Flags such as `-funsafe-math-optimizations` do not define `__FAST_MATH__` and cannot be detected in the header.

### A2. Float-domain range check rejects the tool's own decoded values

**Finding.** §3.4 step 2 compares the physical value against the DBC Min/Max. In C that comparison uses the float literal of Max. `decode(raw_max)` is computed by a different operation sequence and can land one ulp above that literal. Then `unpack` followed by `pack` of an unchanged struct returns `ERR_RANGE`.

**Evidence (computed).** In a sample of 112 simple signals (8 to 16 bit, common factors and offsets), 29 have `decode(raw_max) != (float)max`. Examples:

| Signal | `decode(raw_max)` | `(float)max` |
|---|---|---|
| 10 bit, factor 0.1, offset −40 | 62.30000305 | 62.29999924 |
| 8 bit, factor 0.001 | 0.2550000250 | 0.2549999952 |
| 8 bit, factor 0.007 | 1.785000086 | 1.784999967 |

**Recommendation.** Check the range on the integer raw value, not on the float.

1. The generator computes `RAW_MIN` and `RAW_MAX` exactly from the Fractions: `ceil((min − offset) / factor)` and `floor((max − offset) / factor)`, intersected with the wire range, with the roles swapped for a negative factor.
2. `pack` runs in this order:

   ```c
   if (!(v >= -FLT_MAX && v <= FLT_MAX))   return PT_ERR_NOT_FINITE;  /* NaN and Inf */
   if (!(v >= GUARD_LO && v <= GUARD_HI))  return PT_ERR_RANGE;       /* protects the cast below */
   r = pt_round_i32((v - OFFSET) / FACTOR);                           /* cannot overflow now */
   if (r < RAW_MIN || r > RAW_MAX)         return PT_ERR_RANGE;
   /* optional strict quantization check */
   ```

3. `GUARD_LO` and `GUARD_HI` are exactly representable floats that enclose the valid range with slack. Their only job is to prevent undefined behaviour in the float-to-integer conversion. The checker proves that the rounded value fits the integer type at both guards.
4. The checker's round-trip condition becomes: for every raw in `[RAW_MIN, RAW_MAX]`, `pack(unpack(raw))` returns `OK` and the same raw.
5. Document the consequence: without strict quantization, a value slightly above Max that rounds to `RAW_MAX` is accepted. With strict quantization it is rejected.
6. The optional range check in `unpack` uses the same integer limits.

### A3. "Rohbereich" is not defined

**Finding.** §3.3 classifies over "alle Rohwerte" and makes a range wider than the mantissa `UNREPRESENTABLE`. It does not say whether the range is every bit pattern of the signal or only the raw values inside Min/Max. The two give different results for a wide signal with a narrow Min/Max. §3.4 says `unpack` fills the struct even when the value is out of range, which only makes sense if every bit pattern has a defined result.

**Recommendation.** Use two named terms and one rule.

- **Wire range:** every bit pattern of the signal width.
- **Valid range:** `[RAW_MIN, RAW_MAX]` from A2.
- **Rule for 1.0:** the classification covers the wire range. This is simple, has no hidden conditions, and the remedy (choose `double` for that signal) is one setting.

The alternative is to classify over the valid range only. That requires the range check in `unpack` to be mandatory, and the struct value outside the valid range to be documented as best effort. It helps targets where `double` is expensive (single-precision FPU). I would keep it as a later option.

### A4. Enum-typed struct fields are unsafe

**Finding.** The example in §4 uses `pt_gear_e gear` as a struct field. Four problems:

- With `-fshort-enums` (default for bare-metal ARM EABI) an enum with values 0 to 3 is one byte wide. A raw value outside the table from a wider signal is truncated on assignment. `unpack` can then no longer report what was on the bus.
- C11 requires enumeration constants to fit in `int`. A table entry such as `0xFFFFFFFF` on a 32-bit signal is a constraint violation under `-Wpedantic`.
- Many value tables are incomplete or sit on scaled signals, for example a speed signal with factor 0.25 and one entry `0xFFFF = "SNA"`. Such a signal must not become an enum.
- §3.4 step 5 depends on a table being "als vollständig markiert". §2.4 has no attribute for that.

**Recommendation.**

1. The struct field always has the type from §3.1: a fixed-width integer or the chosen floating-point type.
2. Value tables produce constants only, as `#define PT_GEAR_PARK 0u` and so on. `pt_gear_to_string()` takes the integer type and returns `NULL` for unknown values.
3. For 1.0, emit constants only for unscaled integer signals (factor 1, offset 0). For scaled signals, list the entries in the Doxygen comment and report an info finding.
4. Add a signal attribute `dbc2h_SigEnumComplete` (0/1, default 0). Only with 1 does `pack` return `ERR_ENUM` for values outside the table.
5. Value descriptions are free text and often collide after conversion ("Not available", "not_available"). Resolve such collisions deterministically by appending the value (`PT_GEAR_NOT_AVAILABLE_14`) instead of raising an error. Keep the hard error for message and signal names.

### A5. Attribute names reused across object types

**Finding.** §2.4 defines `dbc2h_FloatType` for both network and signal, with different value sets, and `dbc2h_Ignore` for both message and signal. In DBC, an attribute definition is identified by its name. cantools stores definitions in a dictionary keyed by name, so the second definition replaces the first. I expect CANdb++ to behave the same way, which would put the M5 acceptance criterion at risk.

**Recommendation.**

- Use one name per object type, following Vector's `GenMsg…` / `GenSig…` convention:

  | Object | Attributes |
  |---|---|
  | Network | `dbc2h_Prefix`, `dbc2h_FloatType`, `dbc2h_StrictQuantization`, `dbc2h_FloatEpsLsb`, `dbc2h_OutputMode`, `dbc2h_OutputDir` |
  | Message | `dbc2h_MsgIgnore` |
  | Signal | `dbc2h_SigFloatType`, `dbc2h_SigIgnore`, `dbc2h_SigEnumComplete` |

- Keep every name at 32 characters or fewer.
- Write `BA_DEF_` and `BA_DEF_DEF_` only when the first non-default value is stored, and write `BA_` only for non-default values. A third-party DBC that is merely opened and generated from stays unchanged.
- In M1, load a hand-written file with all `dbc2h_*` attributes into CANdb++ once and record the result. This also settles the "to confirm" mark on this finding.

### A6. Verbatim preservation conflicts with rename and delete

**Finding.** §2.3 keeps unknown or unedited statements verbatim. Many of them refer to objects by name or ID: `SIG_GROUP_`, `BO_TX_BU_`, `CM_`, `BA_`, `VAL_`, `SIG_VALTYPE_`, `SG_MUL_VAL_`, `EV_`-related statements, `BA_REL_`. If the user renames a signal, deletes a node, or changes a message ID, a statement kept as raw text keeps the old reference. The file is then inconsistent, and other tools may refuse it.

**Recommendation.** Sort all statement kinds into three classes and write the class into the parser specification.

| Class | Handling | Examples |
|---|---|---|
| Modelled and editable | Full model objects | `BU_`, `BO_`, `SG_`, `CM_`, `VAL_`, `VAL_TABLE_`, `BA_DEF_`, `BA_`, `SIG_VALTYPE_` |
| Reference-aware | Original text kept, positions of all references known | `SIG_GROUP_`, `BO_TX_BU_`, `SG_MUL_VAL_`, `BA_REL_`, `EV_` family |
| Opaque | Kept verbatim | Anything the parser does not recognise |

- Every command in `core/edit.py` that renames, deletes, or renumbers updates all references in the first two classes within the same undo step. Deleting asks first and lists what else is removed.
- For opaque statements, a rename or delete searches for the old identifier as a token and warns if it occurs.
- Add a lint error "dangling reference".
- Test: for every edit command, write the result, parse it again with dbc2h and with cantools, and assert that no reference dangles.

### A7. `dbc2h.tcss` cannot be loaded from the `.pyz`

**Finding.** Textual reads `CSS_PATH` files with plain `open()` (`textual/css/stylesheet.py`, `Stylesheet.read`). A path inside a zip archive is not a file. The feasibility test in §2.1 passed because Textual's own widgets carry their CSS inline; an app stylesheet was evidently not part of it.

**Evidence (source).** A minimal zipapp with a bundled `.tcss` file: `open()` fails with `NotADirectoryError`, `importlib.resources` reads it correctly.

**Recommendation.**

```python
from importlib.resources import files

class Dbc2hApp(App):
    CSS = files("dbc2h.tui").joinpath("dbc2h.tcss").read_text(encoding="utf-8")
```

- Apply the same pattern to any screen with its own stylesheet.
- When running from a source checkout, `CSS_PATH` may still be set so that Textual's live reload works during development.
- `tools/build_pyz.py` must copy non-Python files (`*.tcss`).
- Rule for the whole package: no file access relative to `__file__`. Run the test suite against the built `.pyz` as well, not only against the source tree.
- Extend the M0 acceptance criterion: the hello-world is styled by a bundled `.tcss` file.

### A8. DBC file hash breaks `gen --check`

**Finding.** §4 writes the sha256 of the DBC file into the header. The bytes of the file are not stable: Git on Windows may convert line endings (`core.autocrlf`), so the same commit yields different hashes on Windows and Linux. `gen --check` would then fail on one platform.

**Recommendation.**

- `gen --check` regenerates in memory and compares bytes with the existing files. It does not need the hash.
- Keep the hash for traceability, but compute it over the content with line endings normalised to LF.
- Always write generated files with LF, regardless of platform.
- Recommend a `.gitattributes` entry in the user documentation: `*.dbc -text` and `gen/*.h text eol=lf`.
- Document that a new dbc2h version makes all headers stale, because the version is in the first line. That is intended.

### A9. Header claims C++ compatibility it does not have

**Finding.** The header has `extern "C"` guards, so it is meant to be included from C++. It also uses `_Generic` (not C++), `_Static_assert` (an extension in C++, not accepted by MSVC in C++ mode), and anonymous structs inside an anonymous union for multiplexing (not standard C++, warns under `-Wpedantic`).

**Recommendation.** Support C++ inclusion; mixed C and C++ projects are common in the target field.

```c
#ifdef __cplusplus
#  define PT_STATIC_ASSERT(cond, msg) static_assert(cond, msg)
#else
#  define PT_STATIC_ASSERT(cond, msg) _Static_assert(cond, msg)
#endif
```

- Emit the `_Generic` macros only inside `#ifndef __cplusplus`.
- Give the multiplex union and its structs names (`msg.mux.m3.signal`). Named members are valid in C and C++.
- In CI, compile the generated header once as C++17 with g++, clang++ and MSVC, with the same warning level as for C.

If C++ is out of scope instead, remove the `extern "C"` block and add `#ifdef __cplusplus` / `#error`.

---

## B. Decisions to make

### B1. Integer path (§3.1 rule 1)

**Open.** The rule covers integer factor and offset, but not what happens with factor ≠ 1 when the value is not a multiple, with a negative factor, or when the physical range exceeds 64 bits.

**Recommendation.**

- `pack` returns `ERR_QUANTIZATION` when `(phys − offset)` is not divisible by the factor. This applies always, not only in strict mode, because an integer has no tolerance.
- The field type is chosen from the physical range over the wire range, computed with Python integers. Minimum and maximum swap for a negative factor.
- If the physical range fits neither `int64_t` nor `uint64_t`, that is a lint error.
- The generated arithmetic must not overflow in intermediate steps. The checker proves this the same way as for floats.

### B2. Multiplexing

**Open.** Extended multiplexing (`SG_MUL_VAL_`, value ranges, nested selectors) appears in the parser tests but not in the generator. "Ein Struct pro Mux-Wert" does not work for ranges.

**Recommendation.**

- 1.0 generates simple multiplexing only: one selector, signals bound to single values.
- A message with extended multiplexing gets a lint error "not supported by the generator". It still round-trips unchanged and can be excluded with `dbc2h_MsgIgnore`.
- The overlap rule in §5 is mux-aware: two signals conflict only if they can be active at the same time.
- The bit-layout editor gets a selector for the mux value shown.
- `pack` writes all DLC bytes. Bits not covered by an active signal are 0. The caller's buffer content never leaks into the frame.

### B3. Third-party DBC files

**Open.** Several rules are hard errors that block generation: Min/Max outside the raw range, identifier collisions. Real OEM files violate them often, and the user may not be allowed to change the file.

**Recommendation.**

- Give every rule a stable ID (for example `E103 minmax-outside-raw`) and print it with each finding.
- Allow severity overrides per rule in a network attribute (`dbc2h_Lint`, a string such as `E103=warning`).
- When `E103` is downgraded, the valid range is the intersection of Min/Max and the wire range.
- Rules that protect correctness cannot be downgraded: numeric `LOSSY`/`UNREPRESENTABLE`, overlap, name collisions of messages and signals. The only way past them is `dbc2h_MsgIgnore` or `dbc2h_SigIgnore` on the element.

### B4. Python version

**Open.** The plan says "Python 3.13" throughout. Python 3.15 is current; this machine runs 3.15.2.

**Recommendation.**

- State the requirement as "Python 3.13 or newer".
- `__main__.py` checks `sys.version_info` first, using only old syntax, and prints a clear message on older interpreters.
- Run the `.pyz` smoke test in CI on 3.13, 3.14 and 3.15 on all three systems.
- Measure the cold start of the TUI from the `.pyz` in M0. Imports from a zip archive are compiled on every start, because no bytecode cache is written.

### B5. Encoding

**Open.** §2.3 tries UTF-8, then cp1252. cp1252 has five undefined bytes (0x81, 0x8D, 0x8F, 0x90, 0x9D), so the fallback itself can fail, for example on files from tools that write GBK or Shift-JIS.

**Recommendation.**

- Decode in three steps: UTF-8 (with BOM detection), then cp1252, then latin-1. latin-1 accepts every byte, so the round-trip is lossless in every case.
- Remember the codec and the BOM, and write with the same.
- Input fields reject characters the file's codec cannot encode. Converting a file to UTF-8 is a separate, explicit action.
- Replace control characters (including 0x80 to 0x9F and ESC) before showing any string from the file. Comments in a DBC are untrusted input for the terminal.
- Before making UTF-8 the default for new files, check how CANdb++ handles it. cp1252 is the safer default given the M5 criterion.

### B6. Data loss

**Open.** The recovery file in §2.1 is written only on an unhandled exception. A dropped SSH session or a closed terminal window is the more likely case for a TUI. There is also no protection against a file that changed on disk while it was open.

**Recommendation.**

- Write the recovery file after every edit command, delayed by one to two seconds, atomically. Delete it on save and on a clean exit.
- Handle `SIGHUP` and `SIGTERM` where the platform has them.
- Record size, modification time and hash at load and at save. Before saving, compare with the disk. On a difference, ask: overwrite, reload, or save under another name.
- On Windows, `os.replace` fails with `PermissionError` while another program holds the file open. Catch it, keep the temporary file, and tell the user.
- Before replacing, resolve symlinks and copy the file mode to the temporary file.

### B7. Order of risk

**Open.** The central claim, that the Python emulation matches compiled C bit for bit, is first tested against real C in M4. M2 builds the checker and M3 the TUI on top of an unvalidated model.

**Recommendation.** Add a formula spike to M2.

- Define the shared formula description as a short list of typed operations (integer to float, multiply, add, subtract, divide, round, float to integer, compare) with two back ends: a Python evaluator and a C emitter.
- A test harness emits a C program for one signal, compiles it with the host compiler, and compares the bits of every result with the emulation.
- Run it over a small flag matrix (`-O0`/`-O2`, contraction off/fast, `c11`/`gnu11`) on x86-64 and arm64.
- New M2 acceptance criterion: emulation and compiled C agree bit for bit for a fixed set of signals on both architectures.

### B8. Round-trip criterion in M1

**Open.** "Bytegleich (bzw. semantisch gleich bei normalisiertem Whitespace)" is two different criteria.

**Recommendation.**

- Load followed by save without edits is byte-identical for the whole corpus.
- After an edit, the diff is limited to the statements affected.
- To get there: every top-level statement keeps its original text and a dirty flag. The writer emits the original text for clean statements and a canonical form for dirty or new ones. New statements go after the last statement of the same kind.

### B9. Large ranges and cost of the check (§3.3)

**Open.** The plan enumerates up to 2²² raw values and uses an analytic bound plus random samples above that. Samples can refute a classification but cannot prove one, and the plan promises proven representability.

**Measured.**

| Work | Time on this machine |
|---|---|
| float32 emulation only, 2²⁰ decodes | 0.24 s |
| Classification with exact error via `Fraction`, extrapolated to 2²² | about 7 s |
| Same with integer arithmetic (`float.as_integer_ratio`), extrapolated to 2²² | about 2 s |
| Same with integer arithmetic, extrapolated to 2²⁴ | about 8 s |

**Recommendation.**

- For `float`, enumerate every signal up to 24 bit. No analytic bound is needed there, since anything wider is `UNREPRESENTABLE` anyway.
- Measure the error with integer arithmetic, not with `Fraction` objects per value.
- Cache results by (width, signedness, factor, offset, type, ε). DBC files repeat signal shapes.
- Keep the analytic bound for `double` only. Evaluate it exactly per binade of the raw value, so that it is tight. It must cover decode and encode.
- If the bound cannot prove the result, report an error (`UNPROVEN`) with the same remedies as `LOSSY`. Use samples only to find a concrete counterexample for the message.
- `struct.pack('<f', x)` raises `OverflowError` for values beyond the float range. `array.array('f', …)` returns `inf` instead and converts in bulk, which also makes it faster.

---

## C. Corrections to the plan text

1. **§3.3, example table.** The values do not match the rules above them.

   | Line | Plan | Computed |
   |---|---|---|
   | `Engine.temp` (8 bit signed, LSB 0.1, offset −40) | max. error 4.7e-7 LSB | 1.5e-5 LSB |
   | `Odo.distance` (32 bit, LSB 0.001, float) | `LOSSY`, 0.5 LSB at raw 16777217 | `UNREPRESENTABLE` by the table (32 > 24 bit) |

   A correct `LOSSY` example: 24 bit unsigned, LSB 0.001, `float` first exceeds 1/16 LSB at raw 683677 (0.063 LSB). The same signal with 19 bit passes (0.055 LSB).

2. **§1.3, signal form.** The mockup is 82 columns wide. §2.1 sets the minimum size to 80×24. Design the form for 80 columns, since the snapshot tests run at that size.

3. **§4, header-only mode.** `pt_gear_to_string` is declared without `static inline` and would need a definition in a `.c` file. The metadata table would be duplicated in every translation unit. Either make both `static inline` / `static const`, or put them behind `#ifdef PT_IMPLEMENTATION`.

4. **§4, generated comments.** The sample mixes German comments ("explizit gewählt", "Wertetabelle") with English error strings. Generated code should use one language; English fits the identifiers.

5. **§7, comparison with cantools.** cantools computes in `double`. Compare raw values and frames, not physical `float` values.

6. **§1.1.** `--version` is used in §7 but not listed among the commands.

---

## D. Where the changes land

| Milestone | Additions from this review |
|---|---|
| M0 | Bundled `.tcss` in the hello-world (A7). Python 3.13 to 3.15 in the smoke test, version check, cold-start measurement (B4). |
| M1 | Statement classes and reference tracking (A6). Attribute names, CANdb++ test file (A5). Byte-identical round-trip (B8). Encoding steps (B5). |
| M2 | Formula description with two back ends and the C spike (B7). FMA variants (A1). Raw-domain range check in the model (A2). Wire range and valid range (A3). Integer path (B1). Rule IDs and overrides (B3). Enumeration strategy (B9). |
| M3 | Recovery after every edit, external-change check, Windows save errors (B6). Display sanitising (B5). |
| M4 | Guards and CI flag matrix (A1). `pack` order (A2). LF-normalised hash, byte-compare in `--check` (A8). C++ compile test (A9). |
| M5 | Dangling-reference handling in the edit commands (A6). Input validation against the file codec (B5). |
| M6 | Mux-aware bit layout (B2). |
| M7 | Value-table constants instead of enum fields, `dbc2h_SigEnumComplete` (A4). Simple multiplexing with named union, full-frame `pack` (B2, A9). |

Text changes in section C can be made in `PLAN.md` right away.

---

## Appendix: how the numbers were produced

All numeric results come from a Python script that emulates binary32 as §3.2 describes and compares against exact `Fraction` arithmetic.

```python
import struct
from fractions import Fraction as F

def f32(x):
    """Round a double to binary32, as in PLAN.md §3.2."""
    return struct.unpack('<f', struct.pack('<f', x))[0]

def rn32(q: F) -> float:
    """Fraction to binary32 with a single rounding (ties to even). Normal range only."""
    if q == 0:
        return 0.0
    s, q = (-1 if q < 0 else 1), abs(q)
    e = q.numerator.bit_length() - q.denominator.bit_length()
    if F(2) ** e > q:
        e -= 1
    ulp = F(2) ** (e - 23)
    n = q / ulp
    fl = n.numerator // n.denominator
    r = n - fl
    if r > F(1, 2) or (r == F(1, 2) and fl % 2 == 1):
        fl += 1
    return s * float(fl * ulp)

def decode(raw, fac, off):       # model of PLAN.md: two operations, two roundings
    return f32(f32(float(raw) * fac) + off)

def decode_fma(raw, fac, off):   # contracted: one rounding of the exact result
    return rn32(F(raw) * F(fac) + F(off))
```

- **A1:** count of raw values with `decode != decode_fma`, where `fac = rn32(factor)` and `off = rn32(offset)`.
- **A2:** `decode(raw_max, …)` compared with `rn32(raw_max · factor + offset)` for 8, 10, 12 and 16 bit, seven factors and four offsets.
- **C1:** maximum of `|decode(raw) − exact| / factor` over the range, and the first raw value above 1/16.
- **B9:** timings of the same loops. `rn32` does not handle subnormals or overflow; a production version must.
- **A7:** a zipapp built with `python -m zipapp` containing a package with one `.tcss` file, read once with `open()` and once with `importlib.resources.files()`.

Compiler behaviour in A1 and A9 was not executed here; it is taken from the GCC, Clang and MSVC documentation and the C11 standard. The B7 spike would confirm A1 on real compilers.
