@echo off
REM ===========================================================================
REM  WII U T6 Studio - build the CONSOLIDATED EXE (fastfiles + texture paks +
REM  sound banks in one window).  Output: dist\WiiU_T6_Studio.exe
REM
REM  This bundle is the UNION of the three previous tools, so it needs every
REM  backend all three needed:
REM    * OAT reference data (T6_Assets.h + ZoneCode XAssets) for the asset walk
REM    * core\_hks_typetable.bin           runtime data for the HKS compiler
REM    * core\_ipak_meta.cache             the ipak name dictionary
REM    * core\_sab_names.cache             the sound bank name dictionary
REM    * numba/llvmlite                    the DSP-ADPCM encoder (sab replace/add)
REM
REM  ????  studio.py imports ff_ide / ipak_ide / sab_ide LAZILY (via __import__, so a
REM  missing backend only breaks its own file type instead of the whole app).
REM  PyInstaller's static analysis cannot see through that, so all three MUST be
REM  named as --hidden-import or the shipped studio opens nothing at all.
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo.
echo === WII U T6 Studio - consolidated release build ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python not found on PATH. Install Python 3.9+ and try again.
    pause
    exit /b 1
)

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [*] Installing PyInstaller...
    python -m pip install --upgrade pyinstaller
    if errorlevel 1 ( echo [!] Failed to install PyInstaller. & pause & exit /b 1 )
)

python -c "import numpy, PIL, scipy, numba" >nul 2>nul
if errorlevel 1 (
    echo [!] numpy, Pillow, scipy and numba are required. Install with:
    echo     python -m pip install numpy pillow scipy numba soundfile
    pause
    exit /b 1
)

set HDR=..\tools\ref_oat\src\Common\Game\T6\T6_Assets.h
set ZCD=..\tools\ref_oat\src\ZoneCode\Game\T6\XAssets
if not exist "%HDR%" ( echo [!] missing %HDR% & pause & exit /b 1 )
if not exist "%ZCD%" ( echo [!] missing %ZCD% & pause & exit /b 1 )

REM  --nogates skips the pre-package batteries for a fast iteration build. The PACKAGED
REM  --selftest at the end still runs: it is the only thing that catches frozen-only faults
REM  (a missing --hidden-import, an unbundled data file), which is exactly what a rebuild
REM  risks. Ship builds should always be made WITHOUT this flag.
if /i "%~1"=="--nogates" (
    echo [!] SKIPPING the pre-package gate batteries ^(--nogates^).
    echo     The packaged binary is still verified after the build.
    goto :skipgates
)

echo [*] Running every gate battery before packaging...
for %%B in (core.xmodel_selftest core.scripts_selftest core.gsc_asm_selftest core.ipak_selftest core.sab_selftest core.zone_media_selftest core.hks_selftest) do (
    echo     - %%B
    python -m %%B >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [!] %%B FAILED - refusing to package. Run it directly to see why:
        echo        python -m %%B
        pause
        exit /b 1
    )
)
echo     - studio shell
python studio.py --selftest --quiet
if errorlevel 1 (
    echo.
    echo [!] Shell gates FAILED - refusing to package.
    pause
    exit /b 1
)

:skipgates
if exist build\WiiU_T6_Studio rmdir /s /q build\WiiU_T6_Studio
taskkill /f /im WiiU_T6_Studio.exe >nul 2>nul

REM  GPL-3.0 compliance: T6_Assets.h and the ZoneCode XAssets embedded below are OpenAssetTools
REM  files (GPL-3.0, no exception granted), so LICENSE and licenses\ are embedded with them. The
REM  licence has to travel INSIDE the binary, not just sit in the repo.
REM  ⚠ Do NOT put REM lines between the caret-continued --add-data flags: inside a continued
REM  command they are not comments, they become arguments to PyInstaller.
echo.
echo [*] Building EXE...
python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name WiiU_T6_Studio ^
    --icon editor.ico ^
    --paths . ^
    --paths ..\wiiu_ref ^
    --paths ..\native_linker ^
    --paths "..\dlc loading\native\fullrelink" ^
    --paths "..\dlc loading\native" ^
    --paths ..\tools ^
    --add-data "%HDR%;." ^
    --add-data "%ZCD%;XAssets" ^
    --add-data "editor.ico;." ^
    --add-data "LICENSE;." ^
    --add-data "licenses;licenses" ^
    --add-data "core\_hks_typetable.bin;core" ^
    --add-data "core\_ipak_meta.cache;core" ^
    --add-data "core\_sab_names.cache;core" ^
    --add-data "core\_zone_images.cache;core" ^
    --hidden-import ff_ide ^
    --hidden-import ipak_ide ^
    --hidden-import sab_ide ^
    --hidden-import core.theme ^
    --hidden-import core.uiutil ^
    --hidden-import core.workspace ^
    --hidden-import core.settings ^
    --hidden-import zone_walk ^
    --hidden-import zone_facts ^
    --hidden-import zone_gates ^
    --hidden-import body_relayout ^
    --hidden-import walker ^
    --hidden-import patch_relink ^
    --hidden-import stage1_roundtrip ^
    --hidden-import ipak ^
    --hidden-import gx2_texture ^
    --hidden-import ipak_stream ^
    --hidden-import wiiu_ff ^
    --hidden-import zone_stream ^
    --hidden-import struct_layout ^
    --hidden-import wiiu_zone ^
    --hidden-import ui_splice ^
    --hidden-import add_asset ^
    --hidden-import lua_endian ^
    --hidden-import sab_convert ^
    --hidden-import core.assets ^
    --hidden-import core.session ^
    --hidden-import core.relink ^
    --hidden-import core.verify ^
    --hidden-import core.scripts ^
    --hidden-import core.scripts_selftest ^
    --hidden-import core.gsc ^
    --hidden-import core.gsc_lang ^
    --hidden-import core.gsc_codegen ^
    --hidden-import core.gsc_assembler ^
    --hidden-import core.gsc_asm_selftest ^
    --hidden-import core.hks ^
    --hidden-import core.hks_dis ^
    --hidden-import core.hks_patch ^
    --hidden-import core.hks_selftest ^
    --hidden-import core.hks_codegen ^
    --hidden-import core.lua_lang ^
    --hidden-import core.xmodel ^
    --hidden-import core.render3d ^
    --hidden-import core.xmodel_selftest ^
    --hidden-import core.ipak ^
    --hidden-import core.ipak_image ^
    --hidden-import core.ipak_names ^
    --hidden-import core.ipak_search ^
    --hidden-import core.zone_images ^
    --hidden-import core.zone_sounds ^
    --hidden-import core.rpl_patch ^
    --hidden-import core.bulk_replace ^
    --hidden-import canonical_gate ^
    --hidden-import reloc_model ^
    --hidden-import core.uiutil ^
    --hidden-import rpl_ide ^
    --hidden-import core.zone_media_selftest ^
    --hidden-import gfximage_census ^
    --hidden-import core.ipak_selftest ^
    --hidden-import core.sab ^
    --hidden-import core.sab_names ^
    --hidden-import core.sab_selftest ^
    --hidden-import scipy.signal ^
    --hidden-import soundfile ^
    --hidden-import PIL.ImageTk ^
    --collect-submodules numba ^
    --collect-submodules llvmlite ^
    --exclude-module matplotlib ^
    --exclude-module pytest ^
    studio.py

if errorlevel 1 ( echo. & echo [!] Build failed - see output above. & pause & exit /b 1 )

echo.
echo [*] Verifying the packaged binary...
dist\WiiU_T6_Studio.exe --selftest --quiet
if errorlevel 1 (
    echo.
    echo [!] The packaged EXE FAILED its own gates - see dist\studio_selftest.log
    pause
    exit /b 1
)

echo.
echo === Done. ===
echo     EXE:  dist\WiiU_T6_Studio.exe
echo.
pause
