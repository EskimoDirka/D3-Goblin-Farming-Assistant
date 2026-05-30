# Goblin Farming App

Windows overlay automation for a Diablo III goblin-farming route.

## Features

- Tk overlay for route teleports, make-new-game, exit-game, repair/salvage, and stash flows.
- Image-template based UI detection using the files in `ImageSearchTemplates`.
- Xbox/XInput controller support with live speed/response tuning.
- Shared combat menu watcher for Monk, Witch Doctor, and Demon Hunter combat loops.
- Optional journal OCR helper in `Tools/GoblinFarmingJournalOcr.ps1`.
- Auto menu close for bounties and the Enchantress follower menu if you're farming and somone joins your game. The only other follower I use to farm is the Templar for my Demon Hunter but haven't had a chance to incorporate his auto close menu
- Will start Battlenet launcher, click play for Diablo III (I only have Diablo III for Battlenet launcher so you may need to adjust this if you have other games installed), then create a new game. Once your character has loaded into the game, it will automatically teleport you to Southern Highlands.
- Will auto click on doors while combat is active and there are click-safe regions on the screen to ensure smooth farming

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
- No gameplay account data is required by this app.
- Number 1 is used to teleport to the next location with logic failsafes in place. For the Xbox 360 S controller use the right trigger to teleport. If you use the hotkey to teleport and you're in a location that needs to be fully cleared, then it will prevent you from teleporting and a splash notification will appear in the Diablo III window telling you to first clear the area. For example, if you're in Cathedral Level 1 and try to use the hotkey to teleport next, you'll be blocked. You won't be able to teleport until you've reached Cathedral Level 3. Overlay teleports shouldn't be affected by this. This hotkey logic failsafe also applies to Cave of the Moon Clan Level 1, City of Caldeum, Sewers of Caldeum, Flooded Causeway, Western Channel Level 1, Eastern Channel Level 1, and Stinging Winds.
- When you get to the end of the farming route, and you use the hotkey to teleport to the "next" location it will automatically run the MakeNewGame() which will teleport you to New Tristram, repair/salvage, leave game, create a new game, then teleport you to Southern Highlands after you've loaded into the game.
- When you start a new game Northern Highlands button on the overlay will be displayed as orange as a reminder that's where you need to teleport to next if using the overlay. If you're using the hotkey, then don't worry about it, it will teleport you there when you press number 1. The next location that you will need to teleport to will be displayed as orange in the overlay and your current location will always be displayed as a green button.
- Number 2 is used to run the Exit Game function which does auto repair/salvage and also stashes any Gibbering Gemstones from your inventory to your stash. There will be a confirmation window to ensure exiting of Diablo. These will need to be adjusted depending on where you store these or comment it out
- It has 3 different combat loops depending on which character you're using to farm. Monk, Demon Hunter, and Witch Doctor. All skills and skill placements on the action bar are listed below if you want an out-of-the-box experience for farming. It will automatically cast skills and for the Witch Doctor, it will scan if your Hex needs to be cast and auto cast so no need for any timers. It will also stop chicken mode to allow for clean teleports
- The hotkey by default for combat is the tilde key "`". For the Xbox 360 S controller, its the left trigger.
- The Up arrow spams right mouse click & alt + ` will spam left mouse click


## Monk Skills, Runes, and Placement

- Number 1 - Epiphany / Insight
- Number 2 - Mystic Ally / Air Ally
- Number 3 - Dashing Strike / Radiance

## Demon Hunter Skills, Runes, and Placement

- Number 1 - Preperation / Focused Mind
- Number 2 - Companion / Bat Companion
- Number 3 - Vengence / Seethe
- Number 4 - Smoke Screen / Displacement

## Witch Doctor Skills, Runes, and Placement

- Number 1 - Hex / Angry Chicken
- Number 2 - Horrify / Stalker
- Number 3 - Spirit Walk / Severance
