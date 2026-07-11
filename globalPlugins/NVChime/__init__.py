"""
NVChime - Custom NVDA Startup Sound Addon
Author: Leo
Version: 1.1
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

addonHandler.initTranslation()

confspec = {
    "mode": "string(default='pack')",
    "packSound": "string(default='chime')",
    "customPath": "string(default='')",
    "exitMode": "string(default='disabled')",
    "exitPackSound": "string(default='chime')",
    "exitCustomPath": "string(default='')",
    "delayMs": "integer(default=1200, min=0, max=5000)",
}
config.conf.spec["NVChime"] = confspec

SOUND_PACK = {
    "chime": "Classic Chime",
    "retro": "Retro Beep",
    "soft": "Soft Bell",
    "dramatic": "Dramatic Hit",
    "horror": "Horror Sting",
    "chill": "Chill Tone",
}


def get_sounds_dir():
    for addon in addonHandler.getRunningAddons():
        if addon.manifest["name"] == "nvChime":
            return os.path.join(addon.path, "sounds")
    return os.path.join(os.path.dirname(__file__), "..", "sounds")


def get_pack_sound_path(sound_id):
    return os.path.join(get_sounds_dir(), sound_id + ".wav")


def play_sound(path, delay_ms=0):
    def _play():
        try:
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
            # Use AudioPurpose.SOUNDS for newer NVDA versions
            try:
                nvwave.playWaveFile(path, asynchronous=True, isSpeechWaveFileCommand=False)
            except TypeError:
                # Older NVDA versions don't have those params
                nvwave.playWaveFile(path)
        except Exception as e:
            import ui
            try:
                ui.message(f"NVChime error: {str(e)}")
            except Exception:
                pass
    threading.Thread(target=_play, daemon=True).start()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(NVChimeSettingsPanel)
        self._triggerSound("startup")

    def _triggerSound(self, event):
        if event == "startup":
            mode = config.conf["NVChime"]["mode"]
            pack_key = config.conf["NVChime"]["packSound"]
            custom_path = config.conf["NVChime"]["customPath"]
            delay = config.conf["NVChime"]["delayMs"]
        else:
            mode = config.conf["NVChime"]["exitMode"]
            pack_key = config.conf["NVChime"]["exitPackSound"]
            custom_path = config.conf["NVChime"]["exitCustomPath"]
            delay = 0

        if mode == "disabled":
            return
        elif mode == "pack":
            path = get_pack_sound_path(pack_key)
            if os.path.isfile(path):
                play_sound(path, delay_ms=delay)
        elif mode == "custom":
            if custom_path and os.path.isfile(custom_path):
                play_sound(custom_path, delay_ms=delay)

    def terminate(self):
        self._triggerSound("exit")
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(NVChimeSettingsPanel)
        super().terminate()


class NVChimeSettingsPanel(gui.settingsDialogs.SettingsPanel):
    title = "NVChime"

    def makeSettings(self, sizer):
        helper = gui.guiHelper.BoxSizerHelper(self, sizer=sizer)

        helper.addItem(wx.StaticText(self, label="Startup Sound"))
        self.modeChoice = helper.addLabeledControl(
            "Startup sound mode:",
            wx.Choice,
            choices=["Play a sound from the pack", "Play my own WAV file", "Disabled"],
        )
        modeMap = {"pack": 0, "custom": 1, "disabled": 2}
        self.modeChoice.SetSelection(modeMap.get(config.conf["NVChime"]["mode"], 0))
        self.modeChoice.Bind(wx.EVT_CHOICE, self._onModeChange)

        packIds = list(SOUND_PACK.keys())
        self.packChoice = helper.addLabeledControl(
            "Pack sound:", wx.Choice, choices=list(SOUND_PACK.values()),
        )
        currentPack = config.conf["NVChime"]["packSound"]
        self.packChoice.SetSelection(packIds.index(currentPack) if currentPack in packIds else 0)

        customSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.customPathField = wx.TextCtrl(self, value=config.conf["NVChime"]["customPath"])
        customSizer.Add(self.customPathField, proportion=1)
        self.browseBtn = wx.Button(self, label="Browse...")
        self.browseBtn.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.customPathField))
        customSizer.Add(self.browseBtn)
        helper.addItem(customSizer)

        self.previewStartBtn = helper.addItem(wx.Button(self, label="Preview Startup Sound"))
        self.previewStartBtn.Bind(wx.EVT_BUTTON, self._onPreviewStart)

        helper.addItem(wx.StaticText(self, label="Exit Sound"))
        self.exitModeChoice = helper.addLabeledControl(
            "Exit sound mode:",
            wx.Choice,
            choices=["Play a sound from the pack", "Play my own WAV file", "Disabled"],
        )
        exitModeMap = {"pack": 0, "custom": 1, "disabled": 2}
        self.exitModeChoice.SetSelection(exitModeMap.get(config.conf["NVChime"]["exitMode"], 2))
        self.exitModeChoice.Bind(wx.EVT_CHOICE, self._onModeChange)

        self.exitPackChoice = helper.addLabeledControl(
            "Exit pack sound:", wx.Choice, choices=list(SOUND_PACK.values()),
        )
        currentExitPack = config.conf["NVChime"]["exitPackSound"]
        self.exitPackChoice.SetSelection(packIds.index(currentExitPack) if currentExitPack in packIds else 0)

        exitCustomSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.exitCustomPathField = wx.TextCtrl(self, value=config.conf["NVChime"]["exitCustomPath"])
        exitCustomSizer.Add(self.exitCustomPathField, proportion=1)
        self.exitBrowseBtn = wx.Button(self, label="Browse...")
        self.exitBrowseBtn.Bind(wx.EVT_BUTTON, lambda e: self._onBrowse(self.exitCustomPathField))
        exitCustomSizer.Add(self.exitBrowseBtn)
        helper.addItem(exitCustomSizer)

        self.previewExitBtn = helper.addItem(wx.Button(self, label="Preview Exit Sound"))
        self.previewExitBtn.Bind(wx.EVT_BUTTON, self._onPreviewExit)

        helper.addItem(wx.StaticText(self, label="Timing"))
        self.delaySpinner = helper.addLabeledControl(
            "Startup delay in milliseconds:", wx.SpinCtrl,
            min=0, max=5000,
            initial=config.conf["NVChime"]["delayMs"],
        )


        self._updateVisibility()
    def _onModeChange(self, event):
        self._updateVisibility()

    def _updateVisibility(self):
        startSel = self.modeChoice.GetSelection()
        self.packChoice.Show(startSel == 0)
        self.customPathField.Show(startSel == 1)
        self.browseBtn.Show(startSel == 1)
        self.previewStartBtn.Show(startSel != 2)

        exitSel = self.exitModeChoice.GetSelection()
        self.exitPackChoice.Show(exitSel == 0)
        self.exitCustomPathField.Show(exitSel == 1)
        self.exitBrowseBtn.Show(exitSel == 1)
        self.previewExitBtn.Show(exitSel != 2)
        self.Layout()

    def _onBrowse(self, targetField):
        with wx.FileDialog(
            self, message="Choose a WAV file",
            wildcard="WAV files (*.wav)|*.wav",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                targetField.SetValue(dlg.GetPath())

    def _onPreviewStart(self, event):
        self._previewSound(self.modeChoice, self.packChoice, self.customPathField)

    def _onPreviewExit(self, event):
        self._previewSound(self.exitModeChoice, self.exitPackChoice, self.exitCustomPathField)

    def _previewSound(self, modeCtrl, packCtrl, pathCtrl):
        packIds = list(SOUND_PACK.keys())
        sel = modeCtrl.GetSelection()
        if sel == 0:
            path = get_pack_sound_path(packIds[packCtrl.GetSelection()])
        elif sel == 1:
            path = pathCtrl.GetValue()
        else:
            gui.messageBox("Sound is set to Disabled.", "NVChime", wx.OK | wx.ICON_INFORMATION)
            return

        if path and os.path.isfile(path):
            play_sound(path, delay_ms=0)
        else:
            gui.messageBox(
                f"Sound file not found:\n{path}",
                "NVChime Preview Error", wx.OK | wx.ICON_ERROR,
            )

    def onSave(self):
        packIds = list(SOUND_PACK.keys())
        modeMap = {0: "pack", 1: "custom", 2: "disabled"}
        config.conf["NVChime"]["mode"] = modeMap[self.modeChoice.GetSelection()]
        config.conf["NVChime"]["packSound"] = packIds[self.packChoice.GetSelection()]
        config.conf["NVChime"]["customPath"] = self.customPathField.GetValue()
        config.conf["NVChime"]["exitMode"] = modeMap[self.exitModeChoice.GetSelection()]
        config.conf["NVChime"]["exitPackSound"] = packIds[self.exitPackChoice.GetSelection()]
        config.conf["NVChime"]["exitCustomPath"] = self.exitCustomPathField.GetValue()
        config.conf["NVChime"]["delayMs"] = self.delaySpinner.GetValue()
