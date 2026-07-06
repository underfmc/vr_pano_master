# -*- coding: utf-8 -*-
"""ComfyUI API client for AI polish pass."""
from __future__ import annotations

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
                pretty = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
            except Exception:
                pretty = body
            raise RuntimeError(f"ComfyUI /prompt HTTP {e.code}: {pretty}") from e

    def upload_image(self, path: Path, subfolder: str = "vr_pano_master", overwrite: bool = True) -> str:
        """Upload local image to ComfyUI and return LoadImage reference."""
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
        """Download a SaveImage output from ComfyUI /view."""
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
        """Find first image in ComfyUI history and save locally."""
        images = self._extract_output_images(history_result)
        if not images:
            return None
        return self.download_image(images[0], out_path)

    @staticmethod
    def _extract_output_images(history_result: Dict[str, Any]) -> List[Dict[str, Any]]:
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
