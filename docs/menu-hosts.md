# Menu rows: screens, hosts and the generated wiring

How a feature gets a row in a menu, and which parts are shared code, generated or hand-written.

## 1. The row: `struct krk_screen` (`krk.h`)

A feature is one `const struct krk_screen`. Pick one of three kinds:

| kind | set | the runner does |
|---|---|---|
| live screen | `.enter`, `.tick` (+ `.exit`, `.tick_ms`) | clears the content area, owns the keypad, handles Back, history pop, key-queue drain, menu-exit timer |
| one-shot action | `.enter`, `.flags = KRK_SF_ONESHOT` | runs `enter()` over the list; report the result with `krk_popup()` |
| setting | `.opt` → `struct krk_opt` | opens the **stock** option level (radio marks) or numeric editor; you write `get`/`set` only (`docs/stock-option-level.md`) |

## 2. Where the row appears: hosts (`struct krk_host`, `krk_core.c`)

The KRAKEN category has its own list (`krk_screens[]` in `krk_core.c`, `krk_ext_screens[]` slots
0..`KRK_EXT_MAX`-1 in `krk_ext.c`). Every other placement appends the row to a **stock** sub-list, called
a host:

| host | items | rows live in | option rows? |
|---|---|---|---|
| APRS (category 9) | 8.. | `krk_ext.c` | yes |
| Call Log (category 4) | 4.. | `krk_core.c` | no: screens only |
| GNSS (category 8) | 5.. | `krk_core.c` | no: screens only |
| Radio Settings (Settings item 0) | 4.. | `krk_ext.c` | yes |
| Channel Set (Settings item 1) | 22.., at most 32 in all | `krk_ext.c` | yes |

All hosts share one implementation: `krk_host_row` (range and pointer checks), `krk_host_open` (option
level or screen), `krk_host_commit` (commit, then refill the list's cells) and `krk_hook_gen` (append
the rows after the stock generator has run). Each host's section in `krk_core.c` keeps only what is
particular to it: how stock hands over the item, and the `restore` that refills its cells. The Radio
Settings latch (`docs/settings-submenu.md`) is one such particular.

Other Func (`krk_vox.c`, `docs/otherfunc-submenu.md`) is not a host. It nests child lists under a
stock parent, which is a different mechanism (a `nest`, section 3). One Channel Set row is a nest parent too (Burst Voice,
2026-11-06, `docs/chanset-submenu.md` 8): it stays a host row, with its own entries in the host's jump tables (section 3,
"stubs"). The 11th top-level category is also
special-cased in the spec, as a host with naming overrides (section 3).

## 3. The single source: `patches/kraken_ui/menu_hosts.json`

A host row used to be written in up to six places that had to agree. Now it is written once, in the
spec, and `tools/gen_menu_hosts.py` writes the rest:

- in `kraken_ui.patch.json`: the label strings, both language arrays, both descriptor
  pointers/counts, the relocated dispatch jump tables, and their `cmphsi` bounds and pool words
  (existing entries replaced in place by name, other entries untouched);
- `krk_menu_rows.h`: `KRK_<HOST>_FIRST`, `_MAX`, `_GEN_MODE`, `_ITEM_<ROW>` and `_ROWS`, used by
  `krk_core.c`, `krk_ext.c` (designated initializers) and `krk_stubs.S` (dispatch ranges);
- the host's generator-call sites (`gen_sites`: name, addr, why of each stock `bsr menu_option_list_dispatch`),
  as `bsr krk_hook_gen` splices.

The script refuses specs it can't build safely: non-ASCII labels, more than 33 list items, more than 32
rows behind a `cmphsi` bound, overlapping data entries, and generated data outside the
`kraken_ui_data` claim. `tools/build_image.py` runs it with `--check` first and fails if either
output is stale.

### A row with its own jump-table entries (`stubs`, 2026-11-06)

A row of a host that dispatches through `jump_tables` may replace the host's stub in any of them:
`"stubs": {"<table key>": "kraken:<symbol>" | "0x<stock address>"}`. The generator writes that entry instead of `@<host stub>`
for that row and refuses a key that is not one of the host's tables. A raw address reuses a stock case block. The one user is
the Channel Set Burst Voice row: `itemjmp` (the level-4 init) -> `kraken:krk_cs_bv_kids_stub` (its child list) and
`okjmp` (the level-4 OK) -> `0x0304e254`, stock row 15's `set_mode(0x23, 1)` arm (`stubs_why` says why). Its level-5 splices
are lines of `hooks.json` (`cs_l5_*_hook`), like the Other Func level-6 ones.

### Two more kinds in `menu_hosts.json`

Both reuse the host machinery and write only manifest entries (no header lines).

- **The 11th category** is the first entry of `hosts` (`"id": "cat"`). It is a host with: `"pfx": "krk"`
  (data entries are `krk_<table key>`: `krk_catjmp`, `krk_okjmp`, `krk_itemjmp`), `"no_header": true`
  (its constants live in `krk_core.c`, not in `krk_menu_rows.h`), `arr_names` / `desc_names` / a row
  `label_name` for the historical entry names (`krk_labelarr_lang0`, `lang0_label_ptr`, `krk_label`), no
  host-level `labels_at` (the one row pins its address with `label_at`), and **three** `jump_tables`:
  render, OK and item-action, each `{key, at, stock, expect, stub, bound, ptr}`. A table's `bound` is the
  `cmphsi` whose immediate goes from `stock count - 1` to `total - 1` (`09 3a` -> `0a 3a`) and `ptr` is the
  literal-pool word naming the table. The descriptor is `0x030b7db8` (+0 / +4 zh pointer / count, +8 / +12 en).
  Each table's `stub` is the 11th entry (`krk_render_stub`, `krk_ok_stub`, `krk_item_stub` in `krk_stubs.S`).
- **`nests`** (Other Func, `docs/otherfunc-submenu.md`). `slot_base` gives the four tables an item
  number indexes (zh / en label arrays, level-5 init table, level-5 OK table; each slot is `base + 4 * slot`),
  `ok_to` the shared OK target (stock's VOX arm). An `items` entry is one parent row `{slot, label,
  label_name, label_at, stock_name, stock_zh / stock_en (expected label pointers), stub, stock_enter,
  stock_ok, label_why, enter_why, ok_why}` and expands to the label data entry plus the 4-splice template
  `<id>_item<slot>_{lbl_zh, lbl_en, enter, ok}`. `child_lists` is a stock `{pointer, count}` descriptor
  redirected to a relocated, longer array (`vox_kids_*`: both languages' pointer and count splices and the
  two arrays, whose extra row is a stock string kept by pointer).
- The Other Func *sites* stay in `hooks.json` (`of_gen_hook`, `of_l6_*_hook`) with the stubs in `krk_ofstubs.S`, as do the
  Channel Set level-5 sites of the Burst Voice nest (`cs_l5_enter_hook`, `cs_l5_ok_hook`, `cs_l5_title_hook`).

### The other generated manifest and header parts

The same pattern (compact hand-written source, generated output, `--check` in step 0 of the build) covers
three more things, so the manifest is hand-written only where a site or a screen is a finding:

| source | generator | output |
|---|---|---|
| `patches/kraken_ui/hooks.json` | `tools/gen_manifest.py` | the 41 branch-hook splices of `kraken_ui.patch.json` |
| `patches/kraken_ui/link.json` | `tools/gen_manifest.py` | its `pack`: the sources with their full cflags, gc roots, SRAM rules |
| `allocations.json` | `tools/gen_claims.py` | `krk_claims.h` (the pinned claims) |

- `hooks.json`: one line per site `{name, addr, expect | expect_branch, target, why}`. `expect_branch` is a name from
  dm32uv-re's `dm32_stock_symbols.ld` (written `@stock:<name>` in the manifest; a raw `0x` address also works). `t` picks a
  why-template (`band_check`, `parrot_tx`, `parrot_rx`) filled from the hook's own fields; `bias` is the IRAM-load-image
  offset added to `target` (the vocoder-window sites run at flash + -0x030afc20). The five generator-call sites are
  not here but `gen_sites` of their host.
- `link.json`: `defaults` (cflags with `opt`), the `sources` in link order (per file `opt`, extra `cflags`, `keep`), the
  `keep` patterns and the `sram_groups`. `tools/pack_kraken.py` turns the expanded `pack` into the builder's blobs at
  build time ([flash-allocation.md](flash-allocation.md), "How Kraken is placed"); the committed manifest has no `blobs`.
  Kraken symbols are written `@kraken:<symbol>` everywhere in the manifest and the specs.
- `gen_claims.py` writes `krk_claims.h` (`KRK_<CLAIM>_LO/_HI_EXCL`, the claim names without `kraken_`) for the pinned
  claims. Table pointers are checked against program flash (`KRK_PFLASH_LO/HI` in `krk.h`, `ext_ok()`, `krk_ext_hook()`):
  a zero slot is skipped. Until 2026-11-11 they were checked against each blob's range, because a blob could be missing.
- A new hook: add a line to `hooks.json`, run `tools/gen_manifest.py` (new entries are appended to `splices`).

### Adding a row to an existing host

1. Write the screen (section 1) and add its source to `link.json`. Rows of the APRS, Radio Settings and Channel Set
   hosts are entries of `krk_ext.c`'s table: declare the screen `extern` there.
2. Append `{"key", "label", "screen"}` to the host's `rows` in `menu_hosts.json`.
3. APRS only: the analog list is an explicit order, so also add `KRK_APRS_ITEM_<KEY>` to
   `aprs_analog_items[]` in `krk_core.c`. The digital list shows the stock rows only.
4. Run `tools/gen_menu_hosts.py`, then `tools/build.sh`.

Rows already in the spec keep their item numbers, and the tests look rows up by item number, so new
rows go at the end. Removing a row renumbers every row after it.

### Adding a host

The splice **sites** are reverse-engineering work and stay hand-written: where stock calls the row
generator (a `gen_sites` entry of the host), the item-action compare chain or jump table, and the commit path
(lines in `hooks.json`). They go in `krk_stubs.S`, as for the existing hosts (`docs/calllog-submenu.md` is the smallest example).
After that:

1. Add the host to `menu_hosts.json`: descriptor, stock arrays with `expect_*`, `arrays_at`/`labels_at`
   in a free gap of the data claim, `gen_mode` if a generator lists the rows, `jump_tables` if the
   dispatch is a bounded table.
2. In `krk_core.c`: a `restore`, a `static const struct krk_host`, the C entry point the stub calls
   (`krk_host_open(&host, item)`), plus `krk_host_commit` and a `krk_open_opt` arm if it takes
   option rows. If it has a generator, add it to `gen_hosts[]`.
