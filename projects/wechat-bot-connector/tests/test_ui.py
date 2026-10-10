"""Native GUI flows, using synthetic data and no real WeChat account."""
import base64
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

@unittest.skipUnless(os.environ.get("CONNECTOR_GUI_TEST")=="1","requires graphical test session")
class WindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from connector_app import ConnectorWindow,DemoBackend
        from connector_core import Connector
        self.temp=tempfile.TemporaryDirectory()
        self.connector=Connector(self.temp.name,DemoBackend())
        self.root=tk.Tk()
        self.window=ConnectorWindow(self.root,self.connector,demo=True)
        self.root.update()
    def tearDown(self):
        self.window.close()
        self.temp.cleanup()
    def wait_idle(self):
        deadline=time.monotonic()+5
        while self.window.busy and time.monotonic()<deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.window.busy)
        self.root.update()
    def test_restart_button_uses_connector_saved_profile(self):
        self.window.check_saved()
        self.wait_idle()
        self.assertEqual(self.connector.config,Path(self.temp.name)/"reader.json")
        self.assertTrue(self.connector.ready)
    def test_typed_selection_cancels_authorization(self):
        self.connector.inspect()
        token=self.connector.pair(self.connector.create_pairing()["code"])["device_token"]
        self.window.database.set("/synthetic/another-account")
        self.assertFalse(self.connector.authorize(token))
        self.assertFalse(self.connector.ready)
    def test_preview_disables_fields_and_apply_requires_verified_selection(self):
        self.window.database.set("/synthetic/db")
        self.window.source.set("/synthetic/access.json")
        self.window.preview()
        self.assertTrue(all(str(widget.cget("state"))=="disabled" for widget in self.window.pickers))
        self.wait_idle()
        self.assertEqual(str(self.window.apply_button.cget("state")),"normal")
        self.window.apply()
        self.wait_idle()
        self.assertTrue(self.connector.ready)
        self.window.source.set("/synthetic/changed.json")
        self.assertEqual(str(self.window.apply_button.cget("state")),"disabled")
    def test_pair_stop_revoke_and_window_layout(self):
        self.connector.inspect()
        self.window.consent.set(True)
        self.window.start_pairing()
        self.assertIsNotNone(self.window.gateway)
        token=self.connector.pair(self.window.code.get())["device_token"]
        self.window.revoke()
        self.assertFalse(self.connector.authorize(token))
        self.window.stop()
        self.assertIsNone(self.window.gateway)
        self.assertFalse(self.connector.ready)
        self.window.status_text.set("演示数据库可读")
        self.window.detail.set("使用虚构数据验证窗口和配对流程；未读取真实微信。")
        self.root.update()
        # Layout check: action buttons stay inside the window at its minimum size.
        self.root.geometry("760x720")
        self.root.update()
        for widget in (self.window.saved,self.window.preview_button,self.window.apply_button):
            self.assertLess(widget.winfo_rooty()+widget.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())
        if os.environ.get("CONNECTOR_SCREENSHOT_DIR"):
            from PIL import ImageGrab
            destination=Path(os.environ["CONNECTOR_SCREENSHOT_DIR"])
            destination.mkdir(parents=True,exist_ok=True)
            for index,name in ((0,"access"),(1,"bot")):
                self.window.notebook.select(index)
                self.root.update()
                x,y=self.root.winfo_rootx(),self.root.winfo_rooty()
                image=ImageGrab.grab((x,y,x+self.root.winfo_width(),y+self.root.winfo_height()))
                path=destination/(name+".png")
                image.save(path)
                print("CONNECTOR_SCREENSHOT_"+name.upper()+":"+base64.b64encode(path.read_bytes()).decode())
