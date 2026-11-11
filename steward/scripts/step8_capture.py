"""Portable camera packets and videos for the Step 8 sensor-only replay path (Python 3.8+)."""

import base64
import gzip
import hashlib
import io
import json
import os
import selectors
import time
from pathlib import Path
import subprocess

import mujoco
import numpy as np
from PIL import Image


def write_pipe(stream, data, deadline):
    """Write a POSIX encoder pipe without waiting past the caller's wall deadline."""
    descriptor = stream.fileno()
    os.set_blocking(descriptor, False)
    pending = memoryview(data)
    with selectors.DefaultSelector() as selector:
        selector.register(descriptor, selectors.EVENT_WRITE)
        while pending:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Video pipe exceeded its wall deadline")
            if not selector.select(min(.25,remaining)):
                continue
            try:
                count = os.write(descriptor,pending)
            except BlockingIOError:
                continue
            pending = pending[count:]


def png(pixels):
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class Capture:
    """Render a private data snapshot; calibration contains only known robot geometry."""

    def __init__(self, model, calibration, out, ffmpeg, cameras):
        self.model = model
        self.data = mujoco.MjData(model)
        self.calibration = calibration
        self.out = Path(out)
        self.rgb = self.depth = self.recording = None
        self.cameras = cameras
        self.option = mujoco.MjvOption()
        self.option.geomgroup[0] = False
        self.option.sitegroup[:] = 0
        samples = model.vis.quality.offsamples
        try:
            self.rgb = mujoco.Renderer(model, height=256, width=256)
            model.vis.quality.offsamples = 0
            self.depth = mujoco.Renderer(model, height=256, width=256)
            self.depth.enable_depth_rendering()
            self.recording = Recording(self.out, ffmpeg)
        except BaseException:
            self.close()
            raise
        finally:
            model.vis.quality.offsamples = samples

    def record(self, data, step, phase, *, sensors=True, deadline=None):
        # The pinned native client predates mj_copyData; rendering needs this state only.
        for name in ("qpos", "qvel", "act", "ctrl", "mocap_pos", "mocap_quat"):
            getattr(self.data, name)[:] = getattr(data, name)
        self.data.time = data.time
        mujoco.mj_forward(self.model, self.data)
        packet = {"episodeId": 1, "step": step, "stamp": step * .002,
                  "calibration": self.calibration,
                  "calibrationId": hashlib.sha256(json.dumps(self.calibration, sort_keys=True).encode()).hexdigest(),
                  "encoders": {}, "images": {}, "depthMm": {}}
        joints = ["robot0_joint" + str(i) for i in range(1, 8)] + ["gripper0_finger_joint1", "gripper0_finger_joint2"]
        for name in joints:
            joint = self.data.joint(name)
            packet["encoders"][name] = {"position": float(joint.qpos[0]), "velocity": float(joint.qvel[0])}
        images = []
        for role in ("external", "wrist"):
            self.rgb.update_scene(self.data, camera=self.cameras[role], scene_option=self.option)
            pixels = self.rgb.render().copy()
            images.append(pixels)
            if sensors:
                packet["images"][role] = png(pixels)
                self.depth.update_scene(self.data, camera=self.cameras[role], scene_option=self.option)
                depth = np.clip(np.rint(self.depth.render() * 1000), 0, 65535).astype(np.uint16)
                packet["depthMm"][role] = png(depth)
        self.recording.record(packet, phase, images=images, sensors=sensors, deadline=deadline)
        return packet

    def close(self):
        errors = []
        if self.recording is not None:
            try:
                self.recording.close()
            except Exception as exc:
                errors.append(exc)
        for renderer in (self.rgb, self.depth):
            if renderer is not None:
                try:
                    if renderer._gl_context:
                        renderer._gl_context.make_current()
                    renderer.close()
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise RuntimeError("Camera/encoder cleanup failed: " + "; ".join(map(str, errors))) from errors[0]


class Recording:
    """Write synchronized camera video and selected robot packets without world labels."""

    def __init__(self, out, ffmpeg):
        self.out = Path(out)
        (self.out / "sensors").mkdir()
        self.video = subprocess.Popen([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-n", "-f", "rawvideo",
            "-pixel_format", "rgb24", "-video_size", "512x256", "-framerate", "20", "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(self.out / "external-wrist.mp4")], stdin=subprocess.PIPE)
        self.index = 0

    def record(self, packet, phase, *, images=None, sensors=True, deadline=None):
        if images is None:
            images = [np.asarray(Image.open(io.BytesIO(base64.b64decode(packet["images"][role]))))
                      for role in ("external", "wrist")]
        write_pipe(self.video.stdin,np.concatenate(images,axis=1).tobytes(),
                   deadline if deadline is not None else time.monotonic()+5)
        if sensors:
            saved = dict(packet, capturePhase=phase)
            with gzip.open(self.out / "sensors" / ("%04d.json.gz" % self.index), "wt") as stream:
                json.dump(saved, stream, separators=(",", ":"), allow_nan=False)
        self.index += 1

    def close(self):
        close_error = None
        try:
            self.video.stdin.close()
        except OSError as exc:
            close_error = exc
        try:
            code = self.video.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.video.kill()
            self.video.wait()
            raise RuntimeError("Video encoder exceeded its finalization deadline") from None
        if code or close_error:
            raise RuntimeError("Video encoding failed")
