"""
NVChime - Custom NVDA Startup Sound Addon
Author: Leo
Version: 2.1.0

Features:
- Built-in sound pack + community pack import
- Random sound mode (no-repeat)
- Custom sound with user-defined label
- Schedule mode (time of day)
- Day of week mode
- Startup and exit sounds
- Adjustable delay
- Per-event volume control
- Silent hours (do-not-disturb window)
"""

import globalPluginHandler
import addonHandler
import gui
import config
import wx
import os
import threading
import time
import nvwave
import datetime
import zipfile
import shutil
import wave
import array
import tempfile

addonHandler.initTranslation()

confspec = {
    # Startup
    "mode": "string(default='pack')",          # pack, custom, random, schedule, disabled
    "packSound": "string(default='chime')",
    "customPath": "string(default='')",
    "customLabel": "string(default='My Sound')",
    "delayMs": "integer(default=1200, min=0, max=5000)",
    "startupVolume": "integer(default=100, min=0, max=100)",

    # Exit
    "exitMode": "string(default='disabled')",
    "exitPackSound": "string(default='chime')",
    "exitCustomPath": "string(default='')",
    "exitCustomLabel": "string(default='My Sound')",
    "exitVolume": "integer(default=100, min=0, max=100)",

    # Schedule mode
    "schedMorningSound": "string(default='chime')",
    "schedMorningStart": "integer(default=6)",
    "schedAfternoonSound": "string(default='retro')",
    "schedAfternoonStart": "integer(default=12)",
    "schedEveningSound": "string(default='chill')",
    "schedEveningStart": "integer(default=18)",
    "schedNightSound": "string(default='soft')",
    "schedNightStart": "integer(default=22)",

    # Day of week overrides (0=Mon ... 6=Sun), empty string means no override
    "dowMonday": "string(default='')",
    "dowTuesday": "string(default='')",
    "dowWednesday": "string(default='')",
    "dowThursday": "string(default='')",
    "dowFriday": "string(default='dramatic')",
    "dowSaturday": "string(default='horror')",
    "dowSunday": "string(default='')",

    # Silent hours (do-not-disturb window; no startup/exit sound plays during this range)
    "silentHoursEnabled": "boolean(default=False)",
    "silentHoursStart": "integer(default=22, min=0, max=23)",
    "silentHoursEnd": "integer(default=7, min=0, max=23)",
}
config.conf.spec["NVChime"] = confspec

# Built-in pack
BUILTIN_PACK = {
    "chime": "Classic Chime",
    "retro": "Retro Beep",
    "soft": "Soft Bell",
    "dramatic": "Dramatic Hit",
    "horror": "Horror Sting",
    "chill": "Chill Tone",
}

DOW_KEYS = ["dowMonday", "dowTuesday", "dowWednesday", "dowThursday", "dowFriday", "dowSaturday", "dowSunday"]
DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def get_addon_path():
    for addon in addonHandler.getRunningAddons():
        if addon.manifest["name"] == "nvChime":
            return addon.path
    return os.path.join(os.path.dirname(__file__), "..")


def get_sounds_dir():
    return os.path.join(get_addon_path(), "sounds")


def get_packs_dir():
    """Community packs stored in addon/packs/"""
    path = os.path.join(get_addon_path(), "packs")
    os.makedirs(path, exist_ok=True)
    return path


def get_all_sounds():
    """Returns dict of sound_id -> display_name from built-in + all imported packs."""
    sounds = dict(BUILTIN_PACK)
    packs_dir = get_packs_dir()
    for pack_folder in os.listdir(packs_dir):
        pack_path = os.path.join(packs_dir, pack_folder)
        ini_path = os.path.join(pack_path, "pack.ini")
        if os.path.isdir(pack_path) and os.path.isfile(ini_path):
            try:
                import configparser
                cp = configparser.ConfigParser()
                cp.read(ini_path)
                pack_name = cp.get("pack", "name", fallback=pack_folder)
                for key, val in cp.items("sounds"):
                    full_key = f"{pack_folder}/{key}"
                    sounds[full_key] = f"{val} ({pack_name})"
            except Exception:
                pass
    return sounds


def get_sound_path(sound_id):
    """Resolve a sound_id to an absolute WAV path."""
    if "/" in sound_id:
        # Community pack sound
        pack_folder, sound_name = sound_id.split("/", 1)
        return os.path.join(get_packs_dir(), pack_folder, sound_name + ".wav")
    else:
        return os.path.join(get_sounds_dir(), sound_id + ".wav")


_last_random_sound = None


def pick_random_sound():
    global _last_random_sound
    sounds = get_all_sounds()
    ids = list(sounds.keys())
    import random
    if len(ids) > 1 and _last_random_sound in ids:
        ids = [sid for sid in ids if sid != _last_random_sound]
    sound_id = random.choice(ids)
    _last_random_sound = sound_id
    return sound_id


def pick_schedule_sound():
    now = datetime.datetime.now()
    hour = now.hour
    dow = now.weekday()  # 0=Mon, 6=Sun

    # Check day-of-week override first
    dow_key = DOW_KEYS[dow]
    dow_override = config.conf["NVChime"][dow_key]
    if dow_override:
        return dow_override

    # Schedule by time of day
    night_start = config.conf["NVChime"]["schedNightStart"]
    evening_start = config.conf["NVChime"]["schedEveningStart"]
    afternoon_start = config.conf["NVChime"]["schedAfternoonStart"]
    morning_start = config.conf["NVChime"]["schedMorningStart"]

    if hour >= night_start or hour < morning_start:
        return config.conf["NVChime"]["schedNightSound"]
    elif hour >= evening_start:
        return config.conf["NVChime"]["schedEveningSound"]
    elif hour >= afternoon_start:
        return config.conf["NVChime"]["schedAfternoonSound"]
    else:
        return config.conf["NVChime"]["schedMorningSound"]


def apply_volume(src_path, volume_percent, cache_key):
    """Returns a path to play at the given volume (0-100). Scales 16-bit PCM
    WAV samples into a cached temp file; falls back to the original file for
    other formats or on any error."""
    if volume_percent >= 100:
        return src_path
    try:
        with wave.open(src_path, "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        if params.sampwidth != 2:
            return src_path
        samples = array.array("h", frames)
        factor = max(0, min(100, volume_percent)) / 100.0
        for i in range(len(samples)):
            samples[i] = int(samples[i] * factor)
        out_path = os.path.join(tempfile.gettempdir(), f"nvchime_{cache_key}.wav")
        with wave.open(out_path, "wb") as out:
            out.setparams(params)
            out.writeframes(samples.tobytes())
        return out_path
    except Exception:
        return src_path


def play_sound(path, delay_ms=0, volume_percent=100, cache_key="tmp"):
    def _play():
        try:
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
            playPath = apply_volume(path, volume_percent, cache_key)
            nvwave.playWaveFile(playPath)
        except Exception:
            pass
    threading.Thread(target=_play, daemon=True).start()


def in_silent_hours():
    if not config.conf["NVChime"]["silentHoursEnabled"]:
        return False
    start = config.conf["NVChime"]["silentHoursStart"]
    end = config.conf["NVChime"]["silentHoursEnd"]
    hour = datetime.datetime.now().hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def resolve_and_play(mode, pack_sound, custom_path, delay_ms=0, volume_percent=100, cache_key="tmp"):
    if mode == "disabled":
        return
    elif mode == "pack":
        path = get_sound_path(pack_sound)
        if os.path.isfile(path):
            play_sound(path, delay_ms, volume_percent, cache_key)
    elif mode == "custom":
        if custom_path and os.path.isfile(custom_path):
            play_sound(custom_path, delay_ms, volume_percent, cache_key)
    elif mode == "random":
        sound_id = pick_random_sound()
        path = get_sound_path(sound_id)
        if os.path.isfile(path):
            play_sound(path, delay_ms, volume_percent, cache_key)
    elif mode == "schedule":
        sound_id = pick_schedule_sound()
        path = get_sound_path(sound_id)
        if os.path.isfile(path):
            play_sound(path, delay_ms, volume_percent, cache_key)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(NVChimeSettingsPanel)
        if not in_silent_hours():
            resolve_and_play(
                config.conf["NVChime"]["mode"],
                config.conf["NVChime"]["packSound"],
                config.conf["NVChime"]["customPath"],
                config.conf["NVChime"]["delayMs"],
                config.conf["NVChime"]["startupVolume"],
                "startup",
            )

    def terminate(self):
        if not in_silent_hours():
            resolve_and_play(
                config.conf["NVChime"]["exitMode"],
                config.conf["NVChime"]["exitPackSound"],
                config.conf["NVChime"]["exitCustomPath"],
                0,
                config.conf["NVChime"]["exitVolume"],
                "exit",
            )
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(NVChimeSettingsPanel)
        super().terminate()


class NVChimeSettingsPanel(gui.settingsDialogs.SettingsPanel):
    title = "NVChime"

    def makeSettings(self, sizer):
        helper = gui.guiHelper.BoxSizerHelper(self, sizer=sizer)
        self._allSounds = get_all_sounds()
        self._soundIds = list(self._allSounds.keys())
        self._soundNames = list(self._allSounds.values())

        MODES = ["Pack sound", "Custom WAV", "Random", "Schedule", "Disabled"]
        MODE_KEYS = ["pack", "custom", "random", "schedule", "disabled"]

        # ── STARTUP ──
        helper.addItem(wx.StaticText(self, label="Startup Sound"))

        self.modeChoice = helper.addLabeledControl("Mode:", wx.Choice, choices=MODES)
        modeIdx = MODE_KEYS.index(config.conf["NVChime"]["mode"]) if config.conf["NVChime"]["mode"] in MODE_KEYS else 0
        self.modeChoice.SetSelection(modeIdx)
        self.modeChoice.Bind(wx.EVT_CHOICE, self._onModeChange)

        self.packChoice = helper.addLabeledControl("Pack sound:", wx.Choice, choices=self._soundNames)
        currentPack = config.conf["NVChime"]["packSound"]
        self.packChoice.SetSelection(self._soundIds.index(currentPack) if currentPack in self._soundIds else 0)

        customSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.customPathField = wx.TextCtrl(self, value=config.conf["NVChime"]["customPath"])
        customSizer.Add(self.customPathField, proportion=1)
        self.browseBtn = wx.Button(self, label="Browse...")
        self.browseBtn.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.customPathField))
        customSizer.Add(self.browseBtn)
        helper.addItem(customSizer)

        self.customLabel = helper.addLabeledControl("Custom sound label:", wx.TextCtrl, value=config.conf["NVChime"]["customLabel"])

        self.startupVolume = helper.addLabeledControl(
            "Volume (%):", wx.SpinCtrl, min=0, max=100,
            initial=config.conf["NVChime"]["startupVolume"],
        )

        self.previewStartBtn = helper.addItem(wx.Button(self, label="Preview Startup Sound"))
        self.previewStartBtn.Bind(wx.EVT_BUTTON, self._onPreviewStart)

        # ── EXIT ──
        helper.addItem(wx.StaticText(self, label="Exit Sound"))

        self.exitModeChoice = helper.addLabeledControl("Exit mode:", wx.Choice, choices=MODES)
        exitModeIdx = MODE_KEYS.index(config.conf["NVChime"]["exitMode"]) if config.conf["NVChime"]["exitMode"] in MODE_KEYS else 4
        self.exitModeChoice.SetSelection(exitModeIdx)
        self.exitModeChoice.Bind(wx.EVT_CHOICE, self._onModeChange)

        self.exitPackChoice = helper.addLabeledControl("Exit pack sound:", wx.Choice, choices=self._soundNames)
        currentExit = config.conf["NVChime"]["exitPackSound"]
        self.exitPackChoice.SetSelection(self._soundIds.index(currentExit) if currentExit in self._soundIds else 0)

        exitCustomSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.exitCustomPathField = wx.TextCtrl(self, value=config.conf["NVChime"]["exitCustomPath"])
        exitCustomSizer.Add(self.exitCustomPathField, proportion=1)
        self.exitBrowseBtn = wx.Button(self, label="Browse...")
        self.exitBrowseBtn.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.exitCustomPathField))
        exitCustomSizer.Add(self.exitBrowseBtn)
        helper.addItem(exitCustomSizer)

        self.exitCustomLabel = helper.addLabeledControl("Exit custom sound label:", wx.TextCtrl, value=config.conf["NVChime"]["exitCustomLabel"])

        self.exitVolume = helper.addLabeledControl(
            "Volume (%):", wx.SpinCtrl, min=0, max=100,
            initial=config.conf["NVChime"]["exitVolume"],
        )

        self.previewExitBtn = helper.addItem(wx.Button(self, label="Preview Exit Sound"))
        self.previewExitBtn.Bind(wx.EVT_BUTTON, self._onPreviewExit)

        # ── SCHEDULE ──
        helper.addItem(wx.StaticText(self, label="Schedule Mode Settings"))

        periods = [
            ("Morning", "schedMorningSound", "schedMorningStart"),
            ("Afternoon", "schedAfternoonSound", "schedAfternoonStart"),
            ("Evening", "schedEveningSound", "schedEveningStart"),
            ("Night", "schedNightSound", "schedNightStart"),
        ]
        self._schedSoundChoices = {}
        self._schedStartSpinners = {}
        for period_name, sound_key, start_key in periods:
            choice = helper.addLabeledControl(f"{period_name} sound:", wx.Choice, choices=self._soundNames)
            current = config.conf["NVChime"][sound_key]
            choice.SetSelection(self._soundIds.index(current) if current in self._soundIds else 0)
            self._schedSoundChoices[sound_key] = choice

            spinner = helper.addLabeledControl(f"{period_name} starts at hour (0-23):", wx.SpinCtrl, min=0, max=23, initial=config.conf["NVChime"][start_key])
            self._schedStartSpinners[start_key] = spinner

        # ── DAY OF WEEK ──
        helper.addItem(wx.StaticText(self, label="Day of Week Overrides (leave blank for no override)"))
        self._dowChoices = {}
        dow_options = ["(No override)"] + self._soundNames
        for dow_key, dow_name in zip(DOW_KEYS, DOW_NAMES):
            choice = helper.addLabeledControl(f"{dow_name}:", wx.Choice, choices=dow_options)
            current = config.conf["NVChime"][dow_key]
            if current and current in self._soundIds:
                choice.SetSelection(self._soundIds.index(current) + 1)
            else:
                choice.SetSelection(0)
            self._dowChoices[dow_key] = choice

        # ── DELAY ──
        helper.addItem(wx.StaticText(self, label="Timing"))
        self.delaySpinner = helper.addLabeledControl(
            "Startup delay in milliseconds:", wx.SpinCtrl,
            min=0, max=5000,
            initial=config.conf["NVChime"]["delayMs"],
        )

        # ── SILENT HOURS ──
        helper.addItem(wx.StaticText(self, label="Silent Hours"))
        self.silentHoursEnabled = helper.addItem(wx.CheckBox(self, label="Enable silent hours (no startup or exit sound during this window)"))
        self.silentHoursEnabled.SetValue(config.conf["NVChime"]["silentHoursEnabled"])

        self.silentHoursStart = helper.addLabeledControl(
            "Silent from hour (0-23):", wx.SpinCtrl, min=0, max=23,
            initial=config.conf["NVChime"]["silentHoursStart"],
        )
        self.silentHoursEnd = helper.addLabeledControl(
            "Silent until hour (0-23):", wx.SpinCtrl, min=0, max=23,
            initial=config.conf["NVChime"]["silentHoursEnd"],
        )

        # ── IMPORT PACK ──
        helper.addItem(wx.StaticText(self, label="Community Sound Packs"))
        self.importBtn = helper.addItem(wx.Button(self, label="Import Sound Pack (.nvchime-pack)"))
        self.importBtn.Bind(wx.EVT_BUTTON, self._onImportPack)

        self._updateVisibility()

    def _onModeChange(self, event):
        self._updateVisibility()

    def _updateVisibility(self):
        startSel = self.modeChoice.GetSelection()
        self.packChoice.Show(startSel == 0)
        self.customPathField.Show(startSel == 1)
        self.browseBtn.Show(startSel == 1)
        self.customLabel.Show(startSel == 1)
        self.previewStartBtn.Show(startSel != 4)

        exitSel = self.exitModeChoice.GetSelection()
        self.exitPackChoice.Show(exitSel == 0)
        self.exitCustomPathField.Show(exitSel == 1)
        self.exitBrowseBtn.Show(exitSel == 1)
        self.exitCustomLabel.Show(exitSel == 1)
        self.previewExitBtn.Show(exitSel != 4)

        self.Layout()

    def _onBrowse(self, targetField):
        with wx.FileDialog(self, message="Choose a WAV file", wildcard="WAV files (*.wav)|*.wav", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                targetField.SetValue(dlg.GetPath())

    def _onImportPack(self, event):
        with wx.FileDialog(self, message="Choose a sound pack", wildcard="NVChime packs (*.nvchime-pack)|*.nvchime-pack", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            pack_file = dlg.GetPath()

        try:
            packs_dir = get_packs_dir()
            pack_name = os.path.splitext(os.path.basename(pack_file))[0]
            dest = os.path.join(packs_dir, pack_name)
            if os.path.exists(dest):
                shutil.rmtree(dest)
            with zipfile.ZipFile(pack_file, "r") as z:
                z.extractall(dest)
            gui.messageBox(f"Sound pack '{pack_name}' imported successfully! Restart NVChime settings to see new sounds.", "NVChime", wx.OK | wx.ICON_INFORMATION)
        except Exception as e:
            gui.messageBox(f"Failed to import pack:\n{str(e)}", "NVChime Import Error", wx.OK | wx.ICON_ERROR)

    def _onPreviewStart(self, event):
        MODE_KEYS = ["pack", "custom", "random", "schedule", "disabled"]
        mode = MODE_KEYS[self.modeChoice.GetSelection()]
        pack_id = self._soundIds[self.packChoice.GetSelection()]
        custom_path = self.customPathField.GetValue()
        resolve_and_play(mode, pack_id, custom_path, 0, self.startupVolume.GetValue(), "preview_startup")

    def _onPreviewExit(self, event):
        MODE_KEYS = ["pack", "custom", "random", "schedule", "disabled"]
        mode = MODE_KEYS[self.exitModeChoice.GetSelection()]
        pack_id = self._soundIds[self.exitPackChoice.GetSelection()]
        custom_path = self.exitCustomPathField.GetValue()
        resolve_and_play(mode, pack_id, custom_path, 0, self.exitVolume.GetValue(), "preview_exit")

    def onSave(self):
        MODE_KEYS = ["pack", "custom", "random", "schedule", "disabled"]

        config.conf["NVChime"]["mode"] = MODE_KEYS[self.modeChoice.GetSelection()]
        config.conf["NVChime"]["packSound"] = self._soundIds[self.packChoice.GetSelection()]
        config.conf["NVChime"]["customPath"] = self.customPathField.GetValue()
        config.conf["NVChime"]["customLabel"] = self.customLabel.GetValue()
        config.conf["NVChime"]["delayMs"] = self.delaySpinner.GetValue()
        config.conf["NVChime"]["startupVolume"] = self.startupVolume.GetValue()

        config.conf["NVChime"]["exitMode"] = MODE_KEYS[self.exitModeChoice.GetSelection()]
        config.conf["NVChime"]["exitPackSound"] = self._soundIds[self.exitPackChoice.GetSelection()]
        config.conf["NVChime"]["exitCustomPath"] = self.exitCustomPathField.GetValue()
        config.conf["NVChime"]["exitCustomLabel"] = self.exitCustomLabel.GetValue()
        config.conf["NVChime"]["exitVolume"] = self.exitVolume.GetValue()

        for sound_key, choice in self._schedSoundChoices.items():
            config.conf["NVChime"][sound_key] = self._soundIds[choice.GetSelection()]
        for start_key, spinner in self._schedStartSpinners.items():
            config.conf["NVChime"][start_key] = spinner.GetValue()

        for dow_key, choice in self._dowChoices.items():
            sel = choice.GetSelection()
            config.conf["NVChime"][dow_key] = self._soundIds[sel - 1] if sel > 0 else ""

        config.conf["NVChime"]["silentHoursEnabled"] = self.silentHoursEnabled.GetValue()
        config.conf["NVChime"]["silentHoursStart"] = self.silentHoursStart.GetValue()
        config.conf["NVChime"]["silentHoursEnd"] = self.silentHoursEnd.GetValue()
