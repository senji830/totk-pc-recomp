# Upstream Attribution & Patch Notes (`0.5.0+afae80c9-patch9`)

## Original Source & Licensing

All original TotK Recomp code, architecture, lifter, runtime, installer, and documentation belong to **Valiantsin Dzerakh** ([@valentderah](https://github.com/valentderah)) and upstream contributors:

- **Original Repository:** https://github.com/valentderah/totk-pc-recomp
- **Base Release / Commit:** `0.5.0` (`afae80c908ab9fc9cdbafa9ed5295c946d80aa21`)
- **License:** [MIT License](LICENSE) — `Copyright (c) 2026 Valiantsin Dzerakh` (preserved unmodified)
- **Third-Party Code & Bundled Toolchain Licenses:** Preserved unmodified in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the release `licenses/` directory, including:
  - [LAS Recomp](https://github.com/peiikai/LASRecomp) (MIT)
  - [MacNativeGames](https://github.com/ly05010419/MacNativeGames) (MIT)
  - [d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) & [nxnative](https://github.com/seedds/nxnative) (MIT)
  - [SavageWindRecomp](https://github.com/Revivedx/SavageWindRecomp) (MIT)
  - [Ryujinx](https://github.com/Ryujinx/Ryujinx) (MIT)
  - [deko3d](https://github.com/devkitPro/deko3d) (zlib)
  - [hactool](https://github.com/SciresM/hactool) (ISC)
  - [RenoDX](https://github.com/clshortfuse/renodx) (MIT)
  - [SDL 3](https://libsdl.org) (zlib), [Dear ImGui](https://github.com/ocornut/imgui) (MIT), [AMD FidelityFX SDK / FSR 1](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK) (MIT), [Vulkan Memory Allocator](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator) (MIT), [libopus](https://opus-codec.org) (BSD-3-Clause), [libvpx](https://chromium.googlesource.com/webm/libvpx) (BSD-3-Clause + PATENTS), [Zstandard](https://github.com/facebook/zstd) (BSD-3-Clause), [musl](https://git.musl-libc.org/cgit/musl) (MIT), [CPython](https://www.python.org) (PSF-2.0), [llvm-mingw](https://github.com/mstorsjo/llvm-mingw) (Apache-2.0 with LLVM Exception / ISC), [Ninja](https://ninja-build.org) (Apache-2.0), [Sun Valley ttk](https://github.com/rdbende/Sun-Valley-ttk-theme) (MIT).

> **No Nintendo game files or game-derived assets are included** in this repository or in any release archive. Both the source tree and release folder pass `tools/check_content.py` with zero hits.

---

## Patches Applied on Top of `afae80c9`

### 1. Ability Scan-Wave 1-Frame Rainbow/Magenta & HDR Bloom Glitch (upstream `#1064`)
- **Files:** `runtime/vk_texture.c`, `runtime/vk_memory.c`
- **Root Cause:** Under single-threaded split replay (`replay_workers=0`, `split_draws>0`), pool-bound textures (such as the 1920x1080 D16 ability depth target aliasing the 960x540 R11G11B10F lighting target) executed their `UNDEFINED -> GENERAL` layout transition and depth clear in the ahead-of-stream upload batch instead of inline, clobbering live render targets at the same pool offset. Virtual pool remapping (`vk_pool_map_virtual`) also left stale texture entries alive until evicted.
- **Fix:**
  - Record `init_new()` inline for all `e->bound_to` images whenever a replay command buffer is active, with `ALL_COMMANDS_BIT` / `MEMORY_WRITE_BIT` source synchronization on `init_image()`.
  - Track remapped virtual page ranges in `vk_pool_map_virtual()` and invalidate stale textures via `vk_texture_range_gone()` after releasing `pool_lock`.

### 2. Quick Weapon-Swap Frametime Ringing (`reflex=boost` + `frame_queue=1`) & FPS Overlay Accuracy
- **Files:** `runtime/gfx_present.c`, `runtime/nvn_api_obj.c`, `runtime/ui/menu.cpp`
- **Root Cause:**
  - `vk_reflex_sleep()` and `vk_reflex_marker()` held `g_pl` across synchronous NVIDIA `vkLatencySleepNV()` sleeps, blocking `present_main` and input polling.
  - Passing non-zero `minimumIntervalUs = 1/hz` to Reflex while `frame_queue = 1` drained the back-edge pacer queue during light menu frames and caused frametime oscillation (`23–43 ms` at 30 fps) on menu-to-world weapon swaps.
  - `perf_tick()` in `runtime/ui/menu.cpp` measured pre-pacing blit submission intervals instead of actual display flips.
- **Fix:**
  - Introduce dedicated `g_sc_rw` shared/exclusive lock (`g_pl -> g_sc_rw -> g_ql`) protecting `g_sc` lifetime so Reflex sleep/markers never block `present_main`.
  - Set `minimumIntervalUs = 0` when `TOTK_FRAME_QUEUE` is enabled (preserving `lowLatencyMode` and `lowLatencyBoost` without fighting `frame_queue = 1`).
  - Record `PERF_PACE` around `vk_reflex_sleep()` and expose `gfx_last_flip_ns()` for accurate post-pacing display FPS reporting in `menu.cpp`.

### 3. Shrine Entrance Cobblestone Floor Flickering Black Squares (upstream `#530` / `#1134`, adapted from PR `#690`)
- **Files:** `runtime/vk_exec_draw.c`, `runtime/cfg.c`, `totk.ini.example`
- **Root Cause:** Shrine floor material-blend draws sample and write the same `R32_SFLOAT` linear-depth attachment within a single draw call; a draw-to-draw feedback barrier cannot order intra-draw reads against writes from neighboring fragments, producing flickering black tiles.
- **Fix:**
  - Snapshot eligible writable `R32_SFLOAT` feedback attachments into a reusable pool (`FB_COPIES=8`) once per pass (`TOTK_FEEDBACK_R32_COPY=1`, default on; `0` off; `2` snapshot every eligible draw; `TOTK_FEEDBACK_DEBUG=1` for telemetry).
  - Optimized for `0.5.0` `TOTK_BIND_INC=1` with zero-copy fast path on non-feedback draws, lock-free `VkImage` return from `tex_info_lookup()`, `RS.h[32]` capacity bump, and deterministic `feedback_reads_sig` hashing in both `bind_sig()` and `bi_gt()` (`0` mismatches in `TOTK_BIND_INC=check` and `+0.029 ms` / `+0.32%` replay delta in uncapped A/B/B/A testing).

### 4. Non-Blocking Pipeline Cache Autosave on Non-Intel GPUs (upstream `#681`)
- **Files:** `runtime/vk_pipeline.c`
- **Root Cause:** `cache_use` exclusive locking around `vkGetPipelineCacheData()` was added specifically for an Intel Iris Xe `igvk64.dll` driver crash (`#681`), but blocked concurrent pipeline creation during background autosaves on NVIDIA/AMD GPUs every 20 seconds.
- **Fix:**
  - Scope `cache_use` locking to Intel GPUs (`vendorID == 0x8086`), allow concurrent `vkGetPipelineCacheData()` with headroom + up to 3 `VK_INCOMPLETE` retries on non-Intel GPUs, and raise the background autosave interval from `20 s` to `60 s`.

### 5. Cutscene & New-Scene Black Objects / Screen During Pipeline Builds (refined in `patch6`)
- **Files:** `runtime/vk.h`, `runtime/vk_exec_draw.c`, `runtime/vk_pipeline.c`, `runtime/cfg.c`, `totk.ini.example`
- **Root Cause:**
  - `TOTK_PIPE_SKIP` defaulted to `fullscreen` (`2`), which skipped full-screen lighting/composite passes and all 3D geometry draws (`count > 6`) into recent render targets while background workers compiled missing shader/GPL pipeline state. At the start of in-engine cutscenes (where the scene reuses recent color/depth buffers while introducing new actor shaders/materials), skipping those draws rendered characters, objects, or the entire frame black for several frames until background builds completed.
  - Under `strict` (`0`, tested in `patch5`), passes where the color target was rendered every frame while the depth target rotated triggered false `one-shot` `30,000 ms` waits (`241 ms` spike), and pipelines incompatible with GPL (`!p->lay->set1 || k.fbl`, e.g. feedback loops) wasted time in `lib_wait()` before blocking the render thread on monolithic `create_graphics()` (`40+ ms`).
  - Boot-time stage library prebuilding (`lb_start()`) also skipped warming graphics variants into the driver disk cache whenever `TOTK_LIB_PREBUILD_KEEP=0`.
- **Fix:**
  - Set default `PIPE_SKIP` to `any` (`1`) with a tight `25 ms` per-frame wait budget (`80 ms` in `strict`), preventing false `one-shot` stalls on depth-buffer swaps while still protecting genuine one-shot passes (`color_first == 1` or no recent target).
  - Add `vk_pipeline_graphics_gpl_async()` (`mono_wait = 0`) for 3D geometry draws in `any` mode and guard `lib_wait()`/`gpl_link()` with `p->lay->set1 && !k.fbl`, so GPL-linkable cutscene draws fast-link in the same frame (`0.02–3 ms`) without blocking on slow monolithic compiles for non-GPL feedback draws; cache hits consume zero wait budget.
  - Warm all graphics stage library variants from the warm list (`shader-answers.bin` recorded variants first, followed by `shader-prepared.bin`) into the driver disk cache (`keep = 0`, releasing temporary shader/pipeline objects unless `TOTK_LIB_PREBUILD_KEEP > 0` for `prio == 0`) on idle-priority background threads after boot prewarm.

### 6. First-Miss Shader Translation Freeze (e.g. Autobuild `84x84` Icon One-Shot Render)
- **Files:** `runtime/gfx.h`, `runtime/gfx_shader.c`, `runtime/boot.c`
- **Root Cause:**
  - `totk_xlate.exe --serve` and the `xlate` background workers were started lazily on the first uncached runtime shader. When a warm-cache session encountered its first uncached `one-shot` draw (such as Autobuild rendering a new `84x84` blueprint thumbnail), `gfx_shader_await()` stalled the render thread for over 1 second waiting for `totk_xlate.exe` cold-start.
  - Inside `translate_one()`, synchronous disk persistence (`plat_file_flush`, `plat_file_replace`, `use_store`, and `in_store` using `FlushFileBuffers` / `MOVEFILE_WRITE_THROUGH`) ran before publishing the translated shader to memory and waking `gfx_shader_await()`.
- **Fix:**
  - Add `gfx_shader_warm_svc()` (invoked from `rt_boot()` after boot prewarm when `vk_pipeline_async_on()`) to start `xlate` workers and pre-spawn one idle `totk_xlate.exe --serve` child with an initial handshake on a `PLAT_PRIO_BELOW_NORMAL` background thread without adding boot delay.
  - In `xlate_worker()`, load the translated SPIR-V into memory (`mem_insert` + `use_put`) and broadcast `X.done` to wake `gfx_shader_await()` first, then execute `translate_persist()` (`plat_file_flush`, `plat_file_replace`, `use_store`, `in_store`) afterwards off the render thread's critical path.

### 7. Weapon/Shield-Swap Pipeline Stutter & Weather-Change Full-Screen Pass Protection (`patch8`)
- **Files:** `runtime/vk_exec_draw.c`, `runtime/vk_pipeline.c`
- **Root Cause:**
  - Under `pipe_skip = any`, first-time 3D geometry draws (such as equipping a new weapon or shield after a fresh install) waited synchronously on `lib_wait()` (`2–6 ms` per draw) because `TOTK_LIB_PREBUILD_KEEP` defaulted to `0` (destroying warmed stage library handles from RAM after boot prebuild).
  - The per-frame pipeline wait budget (`D->wait_left_ns`) was tracked per `DrawState` worker rather than shared across `replay_workers`, allowing parallel workers to multiply the per-frame wait.
- **Fix:**
  - Separate 3D geometry draws (`!full`) from full-screen passes (`full`) under `pipe_skip = any`: 3D geometry draws call `vk_pipeline_graphics_gpl_async()` with `wait_ms = 0`, fast-linking immediately (`~0.04 ms`) when both GPL stage libraries are already resident in RAM and handing cold library builds to background workers without stalling the render/replay thread.
  - Keep a shared atomic per-frame wait budget (`g_wait_frame`, `g_wait_left_ns`, default `25 ms` in `any` and `80 ms` in `strict`, overridable via `TOTK_PIPE_WAIT_MS`) for full-screen passes so multi-pass weather/sky/lighting transitions never flash dark/black.
  - Raise the default `TOTK_LIB_PREBUILD_KEEP` from `0` to `1024` for gameplay-recorded variants (`shader-answers.bin`, `prio == 0`) in `lb_start()` (~150–300 MB RAM) so frequently used stage libraries stay resident for instant GPL fast-linking.

### 8. Distant Water Abrupt Color Shift / LOD Transition (`patch9`)
- **Files:** `runtime/romfs.c`, `runtime/cfg.c`, `totk.ini.example`
- **Root Cause:** `Shader/terrain_water.*.bfsha.zs` compiles three `o_terrain_shader_lod` tiers (`0` = near water with indirect refraction, `1`/`2` = mid/far water without it). Beyond the terrain water LOD cutoff distance, switching from LOD 0 to LOD 1/2 drops near refraction and snaps bodies of water abruptly to a flat region blue.
- **Fix:**
  - Add `water_lod_overlay()` in `runtime/romfs.c` (enabled by default via `water_lod_fix = 1` / `TOTK_WATER_LOD_FIX=1`; `0` disables; automatically skipped if a player mod overrides `terrain_water`).
  - At boot, unpack `Shader/terrain_water.*.bfsha.zs` from the player's own RomFS using `Pack/ZsDic.pack.zs`, dynamically parse the BFSHA `o_terrain_shader_lod` / `o_terrain_enable_deformable` / `o_terrain_water_is_quicksand` static options, and remap all 48 non-quicksand LOD 1/2 `ResShaderProgram` entries in place to their matching 1-to-1 LOD 0 entries (identical UBOs, strict-subset samplers, matching RLT pointer slots, identical decompressed size so no `ResourceSizeTable` edit is needed), serving the generated overlay from `<profile>/water_lod/`.

### 9. Shrine Dialogue / Light of Blessing Text Box Background Square under `tex_alias = 1` (upstream `#938`)
- **Files:** `runtime/vk_texture.c`
- **Root Cause:** `alias_compatible()` checked only block byte size (`eb == sb`) and geometry, omitting the `linear_format()` match that `slot_resolve()` already enforces. When TotK reused the same transient pool address for unrelated 32-bit formats (e.g., a live `R32F` target and a new `RGBA8` `ui2d` capture target at the same address), `alias_copy()` copied raw float bits (with non-zero alpha bytes) into the new `RGBA8` capture target instead of zero-clearing it, producing a solid/corrupted square behind blurred shrine text. In addition, `alias_copy()` transitioned all mip levels / array layers of `e` from `UNDEFINED -> GENERAL` while only copying `min(e->levels, src->levels)` and `min(e->layers, src->layers)`, leaving extra mips/layers uninitialised.
- **Fix:**
  - Require `linear_format(e->key.d.format) == linear_format(src->key.d.format)` in `alias_compatible()`, matching `slot_resolve()` so same-format aliases (including sRGB/linear and swizzle views) keep working while cross-format pool reuse falls back cleanly to `init_new()`.
  - Clear `e` to `{0,0,0,0}` before `vkCmdCopyBufferToImage` in `alias_copy()` when `e` has more mip levels or array layers than `src` (and `vk_fix_zero() && !e->bound_to && !e->pub.mirrored && !format_compressed(e->f.vk)`), with `VK_PIPELINE_STAGE_2_CLEAR_BIT` included in the initial image barrier.

---

## Rebuilding the Patched Setup Locally

See [tools/make_patched_setup.py](tools/make_patched_setup.py) to assemble a standalone Windows `TotK-Setup` folder from this patched source tree using an existing `TotK-Setup-0.5.0-windows` folder's pinned toolchain (`python/`, `llvm/`, `bin/`, `data/`, `licenses/`, `runtime/deps/`).
