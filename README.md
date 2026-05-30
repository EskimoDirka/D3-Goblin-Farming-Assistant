# Goblin Farming App

Windows overlay automation for a Diablo III goblin-farming route.

## Features

- Tk overlay for route teleports, make-new-game, exit-game, repair/salvage, and stash flows.
- Image-template based UI detection using the files in `ImageSearchTemplates`.
- Xbox/XInput controller support with live speed/response tuning.
- Shared combat menu watcher for Monk, Witch Doctor, and Demon Hunter combat loops.
- Optional journal OCR helper in `Tools/GoblinFarmingJournalOcr.ps1`.

## Requirements

- Windows
- Python 3.12 or newer
- Diablo III installed locally
- Python packages from `requirements.txt`

Install dependencies:

```powershell
py -3 -m pip install -r requirements.txt
```

## Launch

Run:

```powershell
py -3 GoblinFarming.py
```

or double-click `Start Goblin Farming Python.cmd`.

If Diablo III is not in the default location, set `DIABLO_LAUNCH_PATH` before launching:

```powershell
$env:DIABLO_LAUNCH_PATH = "D:\Games\Diablo III\Diablo III.exe"
py -3 GoblinFarming.py
```

## Notes

- Template images and coordinates are user-calibrated. If your resolution/UI differs, update the relevant images and coordinate text files under `ImageSearchTemplates`.
- This app was built for a dual-monitor setup. If you only use one monitor, adjust the overlay position before relying on the default placement.
- This application was developed and tested on a 27-inch monitor at 2560×1440 (QHD / 1440p) resolution, with Windows display scaling set to 100% and Diablo III running in Windowed Fullscreen mode. Users running 1920×1080, ultrawide resolutions (3440×1440), 4K (3840×2160), non-100% display scaling, or different UI scale settings may need to recalibrate screen coordinates and update image templates for reliable operation.
- Runtime state such as overlay position, controller tuning, and route progress is stored in the user's temp directory.
- The close-follower-menu combat helper is calibrated for the Enchantress follower menu only.
- No gameplay account data is required by this app.
