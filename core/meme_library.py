"""Meme Manager 人格图库的页面管理能力。"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .meme_manager_adapter import MemeManagerPersonaAdapter
from .policy_store import (
    MEME_DEFAULT_LIBRARY_ID,
    MEME_LIBRARY_NAME_MAX,
    MEME_MANAGER_NAMESPACE,
    PolicyConfigError,
)


ALLOWED_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_IMAGES = 500
MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2000


class MemePersonaLibraryManager:
    """管理用户策略插件维护的人格表情包库。"""

    DEFAULT_TARGET_LABEL = "默认图库（Meme Manager 只读）"

    def __init__(self, adapter: MemeManagerPersonaAdapter):
        self.adapter = adapter

    def _default_target(self) -> dict[str, Any]:
        return {
            "target_id": MEME_DEFAULT_LIBRARY_ID,
            "label": self.DEFAULT_TARGET_LABEL,
            "namespace": MEME_MANAGER_NAMESPACE,
            "readonly": True,
        }

    def targets(self) -> list[dict[str, Any]]:
        libraries = self.adapter._libraries()
        named = [
            {
                "target_id": lib_id,
                "label": str(meta.get("name", "") or lib_id),
                "namespace": lib_id,
                "readonly": False,
            }
            for lib_id, meta in libraries.items()
        ]
        named.sort(key=lambda item: item["label"].casefold())
        return [self._default_target(), *named]

    async def describe(self, target_id: Any) -> dict[str, Any]:
        target = self._target(target_id)
        paths = self.adapter.ensure_library(target["namespace"])
        descriptions = self._read_descriptions(paths["data_path"])
        categories = []
        names = set(descriptions)
        if paths["memes_dir"].is_dir():
            names.update(
                item.name
                for item in paths["memes_dir"].iterdir()
                if item.is_dir()
            )
        for name in sorted(names, key=str.casefold):
            category_dir = paths["memes_dir"] / name
            images = []
            if category_dir.is_dir():
                images = [
                    self._image_meta(path, paths["memes_dir"])
                    for path in sorted(
                        category_dir.iterdir(),
                        key=lambda item: item.name.casefold(),
                    )
                    if path.is_file()
                    and path.suffix.casefold() in ALLOWED_IMAGE_EXTENSIONS
                ]
            categories.append(
                {
                    "name": name,
                    "description": str(descriptions.get(name, "") or ""),
                    "images": images,
                }
            )
        result = {
            "target": target,
            "readonly": bool(target.get("readonly")),
            "root_dir": str(paths["data_dir"]),
            "memes_dir": str(paths["memes_dir"]),
            "data_path": str(paths["data_path"]),
            "categories": categories,
        }
        if target.get("readonly") and not paths["memes_dir"].is_dir():
            result["message"] = "未检测到 Meme Manager，默认图库为空。"
        return result

    async def image_preview(
        self,
        target_id: Any,
        category: Any,
        filename: Any,
    ) -> dict[str, Any]:
        target = self._target(target_id)
        category_name = self._validate_category(category)
        paths = self.adapter.ensure_library(target["namespace"])
        category_dir = paths["memes_dir"] / category_name
        self._assert_child(category_dir, paths["memes_dir"])
        path = category_dir / self._safe_filename(filename)
        self._assert_child(path, paths["memes_dir"])
        if not path.is_file():
            raise PolicyConfigError("图片不存在。")
        return self._image_payload(path, paths["memes_dir"])

    async def save_category(
        self,
        target_id: Any,
        category: Any,
        description: Any,
        new_name: Any = None,
    ) -> dict[str, Any]:
        target = self._target(target_id)
        self._readonly_guard(target)
        old_name = self._validate_category(category)
        desired_name = (
            self._validate_category(new_name)
            if new_name is not None and str(new_name).strip()
            else old_name
        )
        paths = self.adapter.ensure_library(target["namespace"])
        source_dir = paths["memes_dir"] / old_name
        target_dir = paths["memes_dir"] / desired_name
        self._assert_child(source_dir, paths["memes_dir"])
        self._assert_child(target_dir, paths["memes_dir"])
        if old_name != desired_name:
            if target_dir.exists():
                raise PolicyConfigError("目标分类已存在。")
            if source_dir.exists():
                source_dir.rename(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        descriptions = self._read_descriptions(paths["data_path"])
        if old_name != desired_name:
            descriptions.pop(old_name, None)
        descriptions[desired_name] = self._normalize_description(description)
        self._write_descriptions(paths["data_path"], descriptions)
        return await self.describe(target_id)

    async def delete_category(
        self,
        target_id: Any,
        category: Any,
    ) -> dict[str, Any]:
        target = self._target(target_id)
        self._readonly_guard(target)
        name = self._validate_category(category)
        paths = self.adapter.ensure_library(target["namespace"])
        category_dir = paths["memes_dir"] / name
        self._assert_child(category_dir, paths["memes_dir"])
        if category_dir.is_dir():
            for path in sorted(
                category_dir.rglob("*"),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                self._assert_child(path, category_dir)
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            category_dir.rmdir()
        descriptions = self._read_descriptions(paths["data_path"])
        descriptions.pop(name, None)
        self._write_descriptions(paths["data_path"], descriptions)
        return await self.describe(target_id)

    async def upload_images(
        self,
        target_id: Any,
        category: Any,
        files: Any,
    ) -> dict[str, Any]:
        if not isinstance(files, list) or not files:
            raise PolicyConfigError("请至少选择一张图片或一个 ZIP 压缩包。")
        target = self._target(target_id)
        self._readonly_guard(target)
        category_name = self._validate_category(category)
        paths = self.adapter.ensure_library(target["namespace"])

        prepared: list[tuple[str, str, bytes]] = []
        archive_count = 0
        skipped = 0
        total_bytes = 0
        for item in files:
            filename = self._safe_upload_filename(
                item.get("name") if isinstance(item, dict) else ""
            )
            raw = item.get("data") if isinstance(item, dict) else ""
            file_bytes = self._decode_data_url(raw)
            if Path(filename).suffix.casefold() == ".zip":
                if len(file_bytes) > MAX_ARCHIVE_BYTES:
                    raise PolicyConfigError("单个 ZIP 压缩包不能超过 50 MB。")
                archive_images, archive_skipped = self._decode_zip_images(
                    file_bytes,
                    category_name,
                )
                prepared.extend(archive_images)
                total_bytes += sum(
                    len(image_bytes)
                    for _, _, image_bytes in archive_images
                )
                archive_count += 1
                skipped += archive_skipped
            else:
                if len(file_bytes) > MAX_IMAGE_BYTES:
                    raise PolicyConfigError("单张图片不能超过 10 MB。")
                prepared.append((category_name, filename, file_bytes))
                total_bytes += len(file_bytes)

            if len(prepared) > MAX_UPLOAD_IMAGES:
                raise PolicyConfigError("单次最多上传 500 张图片。")
            if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
                raise PolicyConfigError("单次上传解压后的图片总量不能超过 100 MB。")

        if not prepared:
            raise PolicyConfigError("没有找到可导入的图片。")

        touched_categories: set[str] = set()
        for image_category, filename, image_bytes in prepared:
            category_dir = paths["memes_dir"] / image_category
            self._assert_child(category_dir, paths["memes_dir"])
            category_dir.mkdir(parents=True, exist_ok=True)
            destination = self._unique_destination(category_dir, filename)
            self._assert_child(destination, category_dir)
            destination.write_bytes(image_bytes)
            touched_categories.add(image_category)

        descriptions = self._read_descriptions(paths["data_path"])
        for image_category in touched_categories:
            descriptions.setdefault(image_category, "")
        self._write_descriptions(paths["data_path"], descriptions)
        data = await self.describe(target_id)
        details = [f"已上传 {len(prepared)} 张图片"]
        if archive_count:
            details.append(f"解压 {archive_count} 个 ZIP")
        if skipped:
            details.append(f"跳过 {skipped} 个非图片文件")
        data["message"] = "，".join(details) + "。"
        return data

    def _decode_zip_images(
        self,
        archive_bytes: bytes,
        default_category: str,
    ) -> tuple[list[tuple[str, str, bytes]], int]:
        images: list[tuple[str, str, bytes]] = []
        skipped = 0
        declared_total = 0
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_ARCHIVE_ENTRIES:
                    raise PolicyConfigError(
                        f"ZIP 内文件条目不能超过 {MAX_ARCHIVE_ENTRIES} 个。"
                    )
                for entry in entries:
                    parts = self._validate_archive_path(entry.filename)
                    if entry.is_dir():
                        continue
                    if entry.flag_bits & 0x1:
                        raise PolicyConfigError("不支持加密的 ZIP 压缩包。")
                    if self._zip_entry_is_symlink(entry):
                        raise PolicyConfigError("ZIP 中不能包含符号链接。")
                    suffix = Path(parts[-1]).suffix.casefold()
                    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
                        skipped += 1
                        continue
                    if entry.file_size > MAX_IMAGE_BYTES:
                        raise PolicyConfigError(
                            f"ZIP 内单张图片不能超过 10 MB：{parts[-1]}"
                        )
                    declared_total += entry.file_size
                    if declared_total > MAX_UPLOAD_TOTAL_BYTES:
                        raise PolicyConfigError(
                            "ZIP 解压后的图片总量不能超过 100 MB。"
                        )
                    category = (
                        default_category
                        if len(parts) == 1
                        else self._validate_category(parts[0])
                    )
                    filename = self._safe_filename(parts[-1])
                    try:
                        image_bytes = archive.read(entry)
                    except (
                        OSError,
                        RuntimeError,
                        NotImplementedError,
                        zipfile.BadZipFile,
                    ) as exc:
                        raise PolicyConfigError(
                            f"读取 ZIP 内图片失败：{parts[-1]}"
                        ) from exc
                    if len(image_bytes) > MAX_IMAGE_BYTES:
                        raise PolicyConfigError(
                            f"ZIP 内单张图片不能超过 10 MB：{filename}"
                        )
                    images.append((category, filename, image_bytes))
        except PolicyConfigError:
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            raise PolicyConfigError("ZIP 压缩包无效或已损坏。") from exc
        return images, skipped

    async def delete_images(
        self,
        target_id: Any,
        category: Any,
        filenames: Any,
    ) -> dict[str, Any]:
        if not isinstance(filenames, list) or not filenames:
            raise PolicyConfigError("请先选择要删除的图片。")
        target = self._target(target_id)
        self._readonly_guard(target)
        category_name = self._validate_category(category)
        paths = self.adapter.ensure_library(target["namespace"])
        category_dir = paths["memes_dir"] / category_name
        self._assert_child(category_dir, paths["memes_dir"])
        deleted = 0
        for filename in filenames:
            path = category_dir / self._safe_filename(filename)
            self._assert_child(path, category_dir)
            if path.is_file():
                path.unlink()
                deleted += 1
        data = await self.describe(target_id)
        data["message"] = f"已删除 {deleted} 张图片。"
        return data

    async def move_images(
        self,
        target_id: Any,
        source_category: Any,
        target_category: Any,
        filenames: Any,
        include_library: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(filenames, list) or not filenames:
            raise PolicyConfigError("请先选择要移动的图片。")
        target = self._target(target_id)
        self._readonly_guard(target)
        source_name = self._validate_category(source_category)
        target_name = self._validate_category(target_category)
        if source_name == target_name:
            raise PolicyConfigError("目标分类不能与当前分类相同。")
        paths = self.adapter.ensure_library(target["namespace"])
        source_dir = paths["memes_dir"] / source_name
        target_dir = paths["memes_dir"] / target_name
        self._assert_child(source_dir, paths["memes_dir"])
        self._assert_child(target_dir, paths["memes_dir"])
        target_dir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for filename in filenames:
            source = source_dir / self._safe_filename(filename)
            self._assert_child(source, source_dir)
            if not source.is_file():
                continue
            destination = self._unique_destination(target_dir, source.name)
            self._assert_child(destination, target_dir)
            source.rename(destination)
            moved += 1
        descriptions = self._read_descriptions(paths["data_path"])
        descriptions.setdefault(target_name, "")
        self._write_descriptions(paths["data_path"], descriptions)
        if not include_library:
            return {"message": f"已移动 {moved} 张图片。"}
        data = await self.describe(target_id)
        data["message"] = f"已移动 {moved} 张图片。"
        return data

    async def copy_images(
        self,
        target_id: Any,
        category: Any,
        filenames: Any,
        dest_target_id: Any,
        include_library: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(filenames, list) or not filenames:
            raise PolicyConfigError("请先选择要复制的图片。")
        source = self._target(target_id)
        dest = self._target(dest_target_id)
        # 源可以是只读的默认库(允许从默认库复制出去);目标不能是只读库。
        self._readonly_guard(dest)
        if source["namespace"] == dest["namespace"]:
            raise PolicyConfigError("源图库和目标图库相同。")
        category_name = self._validate_category(category)
        source_paths = self.adapter.ensure_library(source["namespace"])
        dest_paths = self.adapter.ensure_library(dest["namespace"])
        source_dir = source_paths["memes_dir"] / category_name
        dest_dir = dest_paths["memes_dir"] / category_name
        self._assert_child(source_dir, source_paths["memes_dir"])
        self._assert_child(dest_dir, dest_paths["memes_dir"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for filename in filenames:
            source_file = source_dir / self._safe_filename(filename)
            self._assert_child(source_file, source_dir)
            if not source_file.is_file():
                continue
            destination = self._unique_destination(dest_dir, source_file.name)
            self._assert_child(destination, dest_dir)
            shutil.copy2(source_file, destination)
            copied += 1
        descriptions = self._read_descriptions(dest_paths["data_path"])
        descriptions.setdefault(category_name, "")
        self._write_descriptions(dest_paths["data_path"], descriptions)
        if not include_library:
            return {"message": f"已复制 {copied} 张图片到 {dest['label']}。"}
        data = await self.describe(dest_target_id)
        data["message"] = f"已复制 {copied} 张图片到 {dest['label']}。"
        return data

    def copy_library(self, source_target_id: Any, dest_namespace: str) -> int:
        """把源库(含 MM 只读默认)的全部分类、描述、图片复制到目标命名库。

        返回复制的图片张数。在策略已创建目标命名库后调用。
        """

        source = self._target(source_target_id)
        dest_namespace = str(dest_namespace or "").strip()
        if not dest_namespace or dest_namespace in {
            MEME_DEFAULT_LIBRARY_ID,
            MEME_MANAGER_NAMESPACE,
            "default",
        }:
            raise PolicyConfigError("目标图库无效。")
        if source["namespace"] == dest_namespace:
            raise PolicyConfigError("源图库和目标图库相同。")
        source_paths = self.adapter.ensure_library(source["namespace"])
        dest_paths = self.adapter.ensure_library(dest_namespace)
        descriptions = self._read_descriptions(source_paths["data_path"])
        names = set(descriptions)
        if source_paths["memes_dir"].is_dir():
            names.update(
                item.name
                for item in source_paths["memes_dir"].iterdir()
                if item.is_dir()
            )
        copied = 0
        dest_descriptions = self._read_descriptions(dest_paths["data_path"])
        for name in names:
            category_name = self._validate_category(name)
            source_dir = source_paths["memes_dir"] / category_name
            dest_dir = dest_paths["memes_dir"] / category_name
            self._assert_child(source_dir, source_paths["memes_dir"])
            self._assert_child(dest_dir, dest_paths["memes_dir"])
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_descriptions[category_name] = str(
                descriptions.get(category_name, "") or ""
            )
            if not source_dir.is_dir():
                continue
            for source_file in sorted(source_dir.iterdir()):
                if not source_file.is_file():
                    continue
                if source_file.suffix.casefold() not in ALLOWED_IMAGE_EXTENSIONS:
                    continue
                self._assert_child(source_file, source_dir)
                destination = self._unique_destination(dest_dir, source_file.name)
                self._assert_child(destination, dest_dir)
                shutil.copy2(source_file, destination)
                copied += 1
        self._write_descriptions(dest_paths["data_path"], dest_descriptions)
        return copied

    def _target(self, target_id: Any) -> dict[str, Any]:
        normalized = str(target_id or MEME_DEFAULT_LIBRARY_ID).strip()
        if normalized in {MEME_DEFAULT_LIBRARY_ID, MEME_MANAGER_NAMESPACE, "default"}:
            return self._default_target()
        libraries = self.adapter._libraries()
        meta = libraries.get(normalized)
        if isinstance(meta, dict):
            return {
                "target_id": normalized,
                "label": str(meta.get("name", "") or normalized),
                "namespace": normalized,
                "readonly": False,
            }
        raise PolicyConfigError("未知表情库，无法管理。")

    @staticmethod
    def _readonly_guard(target: dict[str, Any]) -> None:
        if target.get("readonly"):
            raise PolicyConfigError(
                "默认图库为 Meme Manager 只读视图，不能在此修改；"
                "请新建命名图库后操作。"
            )

    @staticmethod
    def validate_library_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise PolicyConfigError("表情库名称不能为空。")
        if len(name) > MEME_LIBRARY_NAME_MAX:
            raise PolicyConfigError(
                f"表情库名称不能超过 {MEME_LIBRARY_NAME_MAX} 个字符。"
            )
        if any(ord(char) < 32 for char in name):
            raise PolicyConfigError("表情库名称不能包含控制字符。")
        return name

    def purge_library_dir(self, lib_id: str) -> None:
        """删除某命名库在磁盘上的目录(在策略已移除该库后调用)。"""

        namespace = str(lib_id or "").strip()
        if not namespace or namespace in {
            MEME_DEFAULT_LIBRARY_ID,
            MEME_MANAGER_NAMESPACE,
            "default",
        }:
            return
        base = self.adapter.root_dir / namespace
        self._assert_child(base, self.adapter.root_dir)
        if not base.is_dir():
            return
        for path in sorted(
            base.rglob("*"),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            self._assert_child(path, base)
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        base.rmdir()

    @staticmethod
    def _read_descriptions(path: Path) -> dict[str, str]:
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyConfigError(f"读取分类描述失败：{exc}") from exc
        if not isinstance(raw, dict):
            raise PolicyConfigError("分类描述文件必须是对象。")
        return {
            str(key): str(value or "")
            for key, value in raw.items()
            if str(key).strip()
        }

    @staticmethod
    def _write_descriptions(path: Path, descriptions: dict[str, str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(descriptions, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            raise PolicyConfigError(f"保存分类描述失败：{exc}") from exc

    @staticmethod
    def _validate_category(value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise PolicyConfigError("分类名称不能为空。")
        if len(name) > 80:
            raise PolicyConfigError("分类名称不能超过 80 个字符。")
        if name in {".", ".."}:
            raise PolicyConfigError("分类名称无效。")
        if any(char in name for char in ("/", "\\")):
            raise PolicyConfigError("分类名称不能包含路径分隔符。")
        if any(ord(char) < 32 for char in name):
            raise PolicyConfigError("分类名称不能包含控制字符。")
        if Path(name).name != name:
            raise PolicyConfigError("分类名称无效。")
        return name

    @staticmethod
    def _normalize_description(value: Any) -> str:
        text = str(value or "").strip()
        if len(text) > 500:
            raise PolicyConfigError("分类描述不能超过 500 个字符。")
        return text

    @staticmethod
    def _safe_filename(value: Any) -> str:
        raw = str(value or "").strip()
        if any(char in raw for char in ("/", "\\")):
            raise PolicyConfigError("图片文件名不能包含路径分隔符。")
        filename = Path(raw).name.strip()
        if not filename:
            raise PolicyConfigError("图片文件名不能为空。")
        if len(filename) > 160:
            raise PolicyConfigError("图片文件名不能超过 160 个字符。")
        if any(ord(char) < 32 for char in filename):
            raise PolicyConfigError("图片文件名不能包含控制字符。")
        if Path(filename).name != filename:
            raise PolicyConfigError("图片文件名无效。")
        suffix = Path(filename).suffix.casefold()
        if suffix not in ALLOWED_IMAGE_EXTENSIONS:
            raise PolicyConfigError("仅支持 png、jpg、jpeg、gif、webp 图片。")
        return filename

    @staticmethod
    def _safe_upload_filename(value: Any) -> str:
        raw = str(value or "").strip()
        if any(char in raw for char in ("/", "\\")):
            raise PolicyConfigError("上传文件名不能包含路径分隔符。")
        filename = Path(raw).name.strip()
        if not filename or len(filename) > 160:
            raise PolicyConfigError("上传文件名无效或过长。")
        if any(ord(char) < 32 for char in filename):
            raise PolicyConfigError("上传文件名不能包含控制字符。")
        suffix = Path(filename).suffix.casefold()
        if suffix != ".zip" and suffix not in ALLOWED_IMAGE_EXTENSIONS:
            raise PolicyConfigError(
                "仅支持 ZIP、png、jpg、jpeg、gif、webp 文件。"
            )
        return filename

    @staticmethod
    def _validate_archive_path(value: Any) -> tuple[str, ...]:
        raw = str(value or "").replace("\\", "/")
        if not raw or raw.startswith("/"):
            raise PolicyConfigError("ZIP 中包含无效路径。")
        parts = PurePosixPath(raw).parts
        if (
            not parts
            or any(part in {"", ".", ".."} for part in parts)
            or parts[0].endswith(":")
            or any(any(ord(char) < 32 for char in part) for part in parts)
        ):
            raise PolicyConfigError("ZIP 中包含越界或无效路径。")
        return parts

    @staticmethod
    def _zip_entry_is_symlink(entry: zipfile.ZipInfo) -> bool:
        unix_file_type = (entry.external_attr >> 16) & 0o170000
        return unix_file_type == 0o120000

    @staticmethod
    def _decode_data_url(value: Any) -> bytes:
        raw = str(value or "")
        if "," in raw and raw.split(",", 1)[0].startswith("data:"):
            raw = raw.split(",", 1)[1]
        if not raw:
            raise PolicyConfigError("图片数据不能为空。")
        try:
            return base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PolicyConfigError("图片数据不是有效的 base64。") from exc

    @staticmethod
    def _unique_destination(directory: Path, filename: str) -> Path:
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        for _ in range(100):
            candidate = directory / f"{stem}_{uuid4().hex[:8]}{suffix}"
            if not candidate.exists():
                return candidate
        raise PolicyConfigError("无法生成不冲突的图片文件名。")

    @classmethod
    def _image_meta(cls, path: Path, memes_dir: Path) -> dict[str, str | int]:
        cls._assert_child(path, memes_dir)
        return {
            "filename": path.name,
            "size": path.stat().st_size,
        }

    @classmethod
    def _image_payload(cls, path: Path, memes_dir: Path) -> dict[str, str | int]:
        cls._assert_child(path, memes_dir)
        data = path.read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        mime = {
            ".gif": "image/gif",
            ".jpeg": "image/jpeg",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(path.suffix.casefold(), "application/octet-stream")
        return {
            "filename": path.name,
            "size": len(data),
            "preview": f"data:{mime};base64,{encoded}",
        }

    @staticmethod
    def _assert_child(path: Path, root: Path) -> None:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise PolicyConfigError("路径越界，已拒绝操作。") from exc
