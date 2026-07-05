# -*- coding: utf-8 -*-
from __future__ import annotations
import copy
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests


class ComfyClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid.uuid4())

    def queue_prompt(self, workflow: Dict[str, Any]) -> str:
        payload = json.dumps({"prompt": workflow, "client_id": self.client_id}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/prompt",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data["prompt_id"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
                pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
            except Exception:
                pretty = body
            raise RuntimeError(f"ComfyUI /prompt HTTP {e.code}: {pretty}") from e

    def upload_image(self, path: Path, subfolder: str = "vr_pano_master", overwrite: bool = True) -> str:
        """Upload local image into ComfyUI input folder and return LoadImage reference.

        ComfyUI LoadImage usually cannot load arbitrary absolute paths from an
        API workflow. It validates filenames against its input directory.
        Therefore the master uploads every project image via /upload/image and
        patches the workflow with the returned relative input reference.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("rb") as f:
            files = {"image": (path.name, f, "application/octet-stream")}
            data = {"overwrite": "true" if overwrite else "false", "subfolder": subfolder}
            r = requests.post(f"{self.base_url}/upload/image", files=files, data=data, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"ComfyUI /upload/image HTTP {r.status_code}: {r.text}")
        info = r.json()
        name = info.get("name") or path.name
        sf = info.get("subfolder") or subfolder or ""
        # LoadImage expects a path relative to ComfyUI/input. For subfolders this
        # is usually 'subfolder/name'.
        return f"{sf}/{name}".replace("\\", "/") if sf else name

    def history(self, prompt_id: str) -> Dict[str, Any]:
        with urllib.request.urlopen(f"{self.base_url}/history/{prompt_id}") as r:
            return json.loads(r.read().decode("utf-8"))

    def queue_and_wait(self, workflow: Dict[str, Any], poll_s: float = 2.0, timeout_s: int = 3600) -> Dict[str, Any]:
        pid = self.queue_prompt(workflow)
        start = time.time()
        while time.time() - start < timeout_s:
            h = self.history(pid)
            if pid in h:
                result = h[pid]
                result["prompt_id"] = pid
                return result
            time.sleep(poll_s)
        raise TimeoutError(f"ComfyUI prompt timeout: {pid}")

    def download_image(self, image_meta: Dict[str, Any], out_path: Path) -> Path:
        """Download a SaveImage output from ComfyUI /view into a local file."""
        params = {
            "filename": image_meta.get("filename", ""),
            "subfolder": image_meta.get("subfolder", ""),
            "type": image_meta.get("type", "output"),
        }
        url = f"{self.base_url}/view?" + urllib.parse.urlencode(params)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as r:
            out_path.write_bytes(r.read())
        return out_path

    def save_first_output_image(self, history_result: Dict[str, Any], out_path: Path) -> Path | None:
        """Find first image in ComfyUI history and save it locally.

        This removes the old manual step where the user had to copy files from
        ComfyUI/output into projects/<project>/comfy_output.
        """
        images = extract_output_images(history_result)
        if not images:
            return None
        return self.download_image(images[0], out_path)


def extract_output_images(history_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    images: List[Dict[str, Any]] = []
    outputs = history_result.get("outputs", {})
    if isinstance(outputs, dict):
        for node_out in outputs.values():
            if not isinstance(node_out, dict):
                continue
            for img in node_out.get("images", []) or []:
                if isinstance(img, dict) and img.get("filename"):
                    images.append(img)
    return images


def patch_workflow_basic(workflow: Dict[str, Any], replacements: Dict[str, str], params: Dict[str, Any]) -> Dict[str, Any]:
    """Generic API-workflow patcher.

    Works if your exported workflow uses placeholder strings in node inputs.

    Supported placeholders by convention:
    - FACE_COLOR: blockout color render for first pass
    - FACE_CANNY: Canny control image
    - STREET_REFERENCE: street panorama/collage image for IP-Adapter
    - FIRST_PASS_IMAGE: first-pass face image for main facade pass/inpaint
    - MAIN_MASK / FACE_MASK: black-white mask for main building
    - SAVE_PREFIX: ComfyUI SaveImage filename_prefix
    - POSITIVE_PROMPT / NEGATIVE_PROMPT
    - MAIN_POSITIVE_PROMPT / MAIN_NEGATIVE_PROMPT
    """
    wf = copy.deepcopy(workflow)
    text = json.dumps(wf, ensure_ascii=False)
    for k, v in replacements.items():
        if v is None:
            continue
        text = text.replace(k, str(v).replace("\\", "/"))
    wf = json.loads(text)

    for node in wf.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        if "steps" in inputs and "steps" in params:
            inputs["steps"] = int(params["steps"])
        if "cfg" in inputs and "cfg" in params:
            inputs["cfg"] = float(params["cfg"])
        if "denoise" in inputs and "denoise" in params:
            inputs["denoise"] = float(params["denoise"])
        if "width" in inputs and "face_size" in params:
            inputs["width"] = int(params["face_size"])
        if "height" in inputs and "face_size" in params:
            inputs["height"] = int(params["face_size"])
        # Model name overrides: keep workflows portable across machines.
        if "ckpt_name" in inputs and params.get("checkpoint"):
            inputs["ckpt_name"] = params["checkpoint"]
        if "control_net_name" in inputs and params.get("controlnet_canny"):
            inputs["control_net_name"] = params["controlnet_canny"]
        if "ipadapter_file" in inputs and params.get("ipadapter"):
            inputs["ipadapter_file"] = params["ipadapter"]
        if "clip_name" in inputs and params.get("clip_vision"):
            inputs["clip_name"] = params["clip_vision"]

        class_type = str(node.get("class_type", ""))

        # First/main pass strength overrides. This lets us tune workflows from
        # config.yaml without editing exported ComfyUI API JSON every time.
        if "ControlNet" in class_type and "strength" in inputs:
            if "controlnet_strength" in params:
                inputs["strength"] = float(params["controlnet_strength"])
            if "controlnet_start" in params and "start_percent" in inputs:
                inputs["start_percent"] = float(params["controlnet_start"])
            if "controlnet_end" in params and "end_percent" in inputs:
                inputs["end_percent"] = float(params["controlnet_end"])

        if "IPAdapter" in class_type and "weight" in inputs:
            if "ipadapter_weight" in params:
                inputs["weight"] = float(params["ipadapter_weight"])
            if "ipadapter_start" in params and "start_at" in inputs:
                inputs["start_at"] = float(params["ipadapter_start"])
            if "ipadapter_end" in params and "end_at" in inputs:
                inputs["end_at"] = float(params["ipadapter_end"])
    return wf
