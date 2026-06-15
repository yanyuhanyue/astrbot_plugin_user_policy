"""Smart ImageChat Hub 人格图库的共享池与页面管理能力。"""

from __future__ import annotations

import base64
import binascii
import io
import json
import shutil
import time
import zipfile
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from .policy_store import (
    SMART_IMAGE_SMART_NAMESPACE,
    SMART_IMAGE_LIBRARY_NAME_MAX,
    SMART_IMAGE_TAG_MAX,
    SMART_IMAGE_TAGS_PER_IMAGE_MAX,
    PolicyConfigError,
)
from .smart_imagechat_adapter import (
    SMART_IMAGE_COLLECTED_SOURCE,
    SMART_IMAGE_EXTERNAL_SOURCE,
    SMART_IMAGE_IMAGEBED_SOURCE,
    SMART_IMAGE_MANUAL_SOURCE,
    SmartImageChatPersonaAdapter,
)


ALLOWED_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_IMAGES = 500
MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2000


class SmartImagePersonaLibraryManager:
    """维护内容寻址图片池和人格图库逻辑成员。"""

    ORIGINAL_LIBRARY_NAME = "Smart ImageChat Hub 全部图库（只读）"
    ORIGINAL_SOURCE_NAMES = {
        SMART_IMAGE_MANUAL_SOURCE: "手动上传图库（只读）",
        SMART_IMAGE_COLLECTED_SOURCE: "自动收集图库（只读）",
        SMART_IMAGE_EXTERNAL_SOURCE: "其他插件图库（只读）",
        SMART_IMAGE_IMAGEBED_SOURCE: "图床同步图库（只读）",
    }
    ORIGINAL_SOURCE_LABELS = {
        SMART_IMAGE_MANUAL_SOURCE: "手动上传",
        SMART_IMAGE_COLLECTED_SOURCE: "自动收集",
        SMART_IMAGE_EXTERNAL_SOURCE: "其他插件",
        SMART_IMAGE_IMAGEBED_SOURCE: "图床同步",
    }
    CAPTION_STATUS_LABELS = {
        "done": "标签已完成",
        "pending": "等待打标",
        "running": "正在打标",
        "failed": "打标失败",
    }

    def __init__(self, adapter: SmartImageChatPersonaAdapter):
        self.adapter = adapter

    def targets(self) -> list[dict[str, Any]]:
        items = []
        target = self.adapter.target or self.adapter._find_target()
        if target is not None:
            originals = self.adapter.original_library_images()
            items.append(
                {
                    "library_id": SMART_IMAGE_SMART_NAMESPACE,
                    "name": self.ORIGINAL_LIBRARY_NAME,
                    "image_count": len(originals),
                    "readonly": True,
                    "sort_order": 0,
                }
            )
            for order, (source, name) in enumerate(
                self.ORIGINAL_SOURCE_NAMES.items(),
                start=1,
            ):
                count = sum(
                    1
                    for item in originals
                    if item.get("library_source") == source
                )
                if not count:
                    continue
                items.append(
                    {
                        "library_id": self.original_library_id(source),
                        "name": name,
                        "image_count": count,
                        "readonly": True,
                        "source": source,
                        "sort_order": order,
                    }
                )
        for library_id, library in self.adapter._libraries().items():
            images = library.get("images", {}) if isinstance(library, dict) else {}
            items.append(
                {
                    "library_id": library_id,
                    "name": str(library.get("name", "") or library_id),
                    "image_count": len(images) if isinstance(images, dict) else 0,
                    "readonly": False,
                    "sort_order": 100,
                }
            )
        return sorted(
            items,
            key=lambda item: (
                not bool(item.get("readonly")),
                int(item.get("sort_order", 100)),
                item["name"].casefold(),
            ),
        )

    def describe(self, library_id: Any) -> dict[str, Any]:
        if self.is_original_library_id(library_id):
            return self._describe_original_library(library_id)
        library_id, library = self._library(library_id)
        images = []
        for digest, item in library.get("images", {}).items():
            path = self._find_pool_file(digest, item.get("ext"))
            images.append(
                {
                    "hash": digest,
                    "filename": str(item.get("filename") or path.name),
                    "ext": str(item.get("ext") or path.suffix.lstrip(".")),
                    "tags": list(item.get("tags", []) or []),
                    "added_at": int(item.get("added_at", 0) or 0),
                    "size": path.stat().st_size if path.is_file() else 0,
                    "available": path.is_file(),
                }
            )
        images.sort(
            key=lambda item: (
                -int(item.get("added_at", 0)),
                str(item.get("filename", "")).casefold(),
            )
        )
        return {
            "library_id": library_id,
            "name": str(library.get("name") or library_id),
            "images": images,
            "readonly": False,
        }

    def image_payload(
        self,
        digest: Any,
        *,
        library_id: Any = "",
        image_id: Any = "",
    ) -> dict[str, Any]:
        if self.is_original_library_id(library_id):
            return self._original_image_payload(
                digest,
                image_id,
                library_id=library_id,
            )
        image_hash = self._hash(digest)
        path = self._find_pool_file(image_hash)
        if not path.is_file():
            raise PolicyConfigError("共享图片池中不存在该图片。")
        data = path.read_bytes()
        return {
            "hash": image_hash,
            "filename": path.name,
            "size": len(data),
            "preview": self._data_url(path.suffix, data),
        }

    def prepare_original_library_copy(
        self,
        library_id: Any = SMART_IMAGE_SMART_NAMESPACE,
    ) -> dict[str, dict[str, Any]]:
        return self.prepare_original_image_copy(library_id, None)

    def prepare_original_image_copy(
        self,
        library_id: Any,
        selections: Any,
    ) -> dict[str, dict[str, Any]]:
        requested: set[tuple[str, str]] | None = None
        if selections is not None:
            if not isinstance(selections, list) or not selections:
                raise PolicyConfigError("请先选择要复制的图片。")
            requested = set()
            for selection in selections:
                if not isinstance(selection, dict):
                    continue
                image_id = str(selection.get("image_id") or "").strip()
                digest = str(selection.get("hash") or "").strip().lower()
                if image_id or self._is_hash(digest):
                    requested.add((image_id, digest))
            if not requested:
                raise PolicyConfigError("选中的图片标识无效。")

        members: dict[str, dict[str, Any]] = {}
        now = int(time.time())
        source = self.original_library_source(library_id)
        for item in self._original_library_items(source):
            if requested is not None and not any(
                (
                    image_id
                    and image_id == item["image_id"]
                    and (not digest or digest == item["hash"])
                )
                or (
                    not image_id
                    and digest
                    and digest == item["hash"]
                )
                for image_id, digest in requested
            ):
                continue
            path = item["path"]
            image_hash, extension = self._store_file(path)
            members[image_hash] = {
                "ext": extension,
                "filename": item["filename"],
                "tags": list(item["tags"]),
                "added_at": now,
            }
        if requested is not None and not members:
            raise PolicyConfigError("选中的 Smart ImageChat Hub 图片已失效。")
        return members

    @classmethod
    def original_library_id(cls, source: str) -> str:
        return f"{SMART_IMAGE_SMART_NAMESPACE}:{source}"

    @classmethod
    def is_original_library_id(cls, library_id: Any) -> bool:
        key = str(library_id or "").strip()
        return (
            key == SMART_IMAGE_SMART_NAMESPACE
            or key in {
                cls.original_library_id(source)
                for source in cls.ORIGINAL_SOURCE_NAMES
            }
        )

    @classmethod
    def original_library_source(cls, library_id: Any) -> str | None:
        key = str(library_id or "").strip()
        if key == SMART_IMAGE_SMART_NAMESPACE:
            return None
        prefix = f"{SMART_IMAGE_SMART_NAMESPACE}:"
        source = key[len(prefix):] if key.startswith(prefix) else ""
        if source not in cls.ORIGINAL_SOURCE_NAMES:
            raise PolicyConfigError("Smart ImageChat Hub 只读图库不存在。")
        return source

    def pending_snapshot(self) -> dict[str, Any]:
        target = self._target()
        snapshot = target._collection_pool_snapshot()
        items = snapshot.get("images", []) if isinstance(snapshot, dict) else []
        enriched = []
        for item in items if isinstance(items, list) else []:
            image_id = str(item.get("id", "") or "").strip()
            raw = target._collection_pool_item_by_id(image_id)
            if not isinstance(raw, dict):
                continue
            enriched.append(
                {
                    **item,
                    "sha256": str(raw.get("sha256", "") or ""),
                    "tags": self._source_tags(raw),
                }
            )
        return {
            **(snapshot if isinstance(snapshot, dict) else {}),
            "images": enriched,
        }

    def pending_image_payload(self, image_id: Any) -> dict[str, Any]:
        target = self._target()
        item = target._collection_pool_item_by_id(str(image_id or "").strip())
        if not isinstance(item, dict):
            raise PolicyConfigError("缓冲图库图片不存在。")
        rel_path = target._norm_rel_path(item.get("rel_path"))
        path = target._abs_plugin_data_path(rel_path)
        if not path.is_file():
            raise PolicyConfigError("缓冲图库图片文件不存在。")
        data = path.read_bytes()
        return {
            "id": str(item.get("id") or image_id),
            "filename": str(item.get("filename") or path.name),
            "preview": self._data_url(path.suffix, data),
            "size": len(data),
        }

    def prepare_pending_distribution(
        self,
        image_ids: Any,
        *,
        inherit_auto_tags: bool,
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        if not isinstance(image_ids, list) or not image_ids:
            raise PolicyConfigError("请先选择缓冲图库图片。")
        target = self._target()
        members: dict[str, dict[str, Any]] = {}
        accepted_ids = []
        for raw_id in image_ids:
            image_id = str(raw_id or "").strip()
            item = target._collection_pool_item_by_id(image_id)
            if not isinstance(item, dict):
                continue
            rel_path = target._norm_rel_path(item.get("rel_path"))
            if not rel_path:
                continue
            source = target._abs_plugin_data_path(rel_path)
            if not source.is_file():
                continue
            image_hash, extension = self._store_file(source)
            members[image_hash] = {
                "ext": extension,
                "filename": str(item.get("filename") or source.name),
                "tags": (
                    self._source_tags(item)
                    if inherit_auto_tags
                    else []
                ),
                "added_at": int(time.time()),
            }
            accepted_ids.append(image_id)
        if not members:
            raise PolicyConfigError("选中的缓冲图片已失效。")
        return members, accepted_ids

    def discard_pending(self, image_ids: list[str]) -> dict[str, Any]:
        return self._target()._discard_pending_collection_images(image_ids)

    def prepare_upload(self, files: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(files, list) or not files:
            raise PolicyConfigError("请至少选择一张图片或一个 ZIP 压缩包。")
        prepared: list[tuple[str, bytes]] = []
        total_bytes = 0
        for item in files:
            filename = self._safe_upload_filename(
                item.get("name") if isinstance(item, dict) else ""
            )
            data = self._decode_data_url(
                item.get("data") if isinstance(item, dict) else ""
            )
            if Path(filename).suffix.casefold() == ".zip":
                if len(data) > MAX_ARCHIVE_BYTES:
                    raise PolicyConfigError("单个 ZIP 压缩包不能超过 50 MB。")
                archive_images = self._decode_zip_images(data)
                prepared.extend(archive_images)
                total_bytes += sum(len(raw) for _, raw in archive_images)
            else:
                if len(data) > MAX_IMAGE_BYTES:
                    raise PolicyConfigError("单张图片不能超过 10 MB。")
                prepared.append((filename, data))
                total_bytes += len(data)
            if len(prepared) > MAX_UPLOAD_IMAGES:
                raise PolicyConfigError("单次最多上传 500 张图片。")
            if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
                raise PolicyConfigError("单次上传图片总量不能超过 100 MB。")
        if not prepared:
            raise PolicyConfigError("没有找到可导入的图片。")

        result: dict[str, dict[str, Any]] = {}
        now = int(time.time())
        for filename, data in prepared:
            image_hash, extension = self._store_bytes(data, Path(filename).suffix)
            result[image_hash] = {
                "ext": extension,
                "filename": filename,
                "tags": self._initial_tags(filename),
                "added_at": now,
            }
        return result

    async def caption_image(self, digest: Any) -> list[str]:
        image_hash = self._hash(digest)
        path = self._find_pool_file(image_hash)
        if not path.is_file():
            raise PolicyConfigError("共享图片池中不存在该图片。")
        target = self._target()
        caption = getattr(target, "_caption_image", None)
        if not callable(caption):
            raise PolicyConfigError(
                "Smart ImageChat Hub 当前版本不支持视觉智能打标。"
            )
        try:
            tags = await caption(path)
        except Exception as exc:
            raise PolicyConfigError(f"Smart 视觉智能打标失败：{exc}") from exc
        normalized = self.normalize_tags(tags)
        return normalized or self._initial_tags(path.name)

    def backup_libraries(self, library_ids: Any) -> tuple[str, bytes]:
        libraries = self.adapter._libraries()
        if library_ids in (None, [], ""):
            selected = list(libraries)
        elif isinstance(library_ids, list):
            selected = [
                str(item)
                for item in library_ids
                if str(item) in libraries
            ]
        else:
            selected = [str(library_ids)] if str(library_ids) in libraries else []
        if not selected:
            raise PolicyConfigError("没有可备份的人格图库。")
        manifest = {
            "version": 1,
            "created_at": int(time.time()),
            "libraries": {
                library_id: libraries[library_id]
                for library_id in selected
            },
        }
        buffer = io.BytesIO()
        written = set()
        with zipfile.ZipFile(
            buffer,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            for library_id in selected:
                images = libraries[library_id].get("images", {})
                for digest, item in images.items():
                    path = self._find_pool_file(digest, item.get("ext"))
                    if not path.is_file() or path.name in written:
                        continue
                    archive.write(path, f"images/{path.name}")
                    written.add(path.name)
        filename = f"smart-image-libraries-{int(time.time())}.zip"
        return filename, buffer.getvalue()

    def purge_unreferenced_pool(self) -> int:
        referenced = {
            digest
            for library in self.adapter._libraries().values()
            if isinstance(library, dict)
            for digest in library.get("images", {})
        }
        removed = 0
        self.adapter.pool_dir.mkdir(parents=True, exist_ok=True)
        for path in self.adapter.pool_dir.iterdir():
            if path.is_file() and path.stem not in referenced:
                path.unlink()
                removed += 1
        return removed

    @staticmethod
    def validate_library_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise PolicyConfigError("智能图片图库名称不能为空。")
        if len(name) > SMART_IMAGE_LIBRARY_NAME_MAX:
            raise PolicyConfigError(
                f"智能图片图库名称不能超过 {SMART_IMAGE_LIBRARY_NAME_MAX} 个字符。"
            )
        if any(ord(char) < 32 for char in name):
            raise PolicyConfigError("智能图片图库名称不能包含控制字符。")
        return name

    @staticmethod
    def normalize_tags(value: Any) -> list[str]:
        if isinstance(value, str):
            values = value.replace("，", ",").replace("\n", ",").split(",")
        elif isinstance(value, list):
            values = []
            for item in value:
                values.extend(
                    str(item or "")
                    .replace("，", ",")
                    .replace("\n", ",")
                    .split(",")
                )
        else:
            raise PolicyConfigError("图片标签必须是数组或逗号分隔文本。")
        result = []
        for item in values:
            tag = str(item or "").strip()
            if not tag or tag in result:
                continue
            if len(tag) > SMART_IMAGE_TAG_MAX:
                raise PolicyConfigError(
                    f"单个图片标签不能超过 {SMART_IMAGE_TAG_MAX} 个字符。"
                )
            result.append(tag)
            if len(result) >= SMART_IMAGE_TAGS_PER_IMAGE_MAX:
                break
        return result

    def _library(self, library_id: Any) -> tuple[str, dict[str, Any]]:
        key = str(library_id or "").strip()
        library = self.adapter._libraries().get(key)
        if not isinstance(library, dict):
            raise PolicyConfigError("智能图片人格图库不存在。")
        return key, library

    def _describe_original_library(self, library_id: Any) -> dict[str, Any]:
        key = str(library_id or "").strip()
        source = self.original_library_source(key)
        images = []
        for item in self._original_library_items(source):
            path = item["path"]
            images.append(
                {
                    "hash": item["hash"],
                    "image_id": item["image_id"],
                    "filename": item["filename"],
                    "ext": path.suffix.lstrip(".").lower(),
                    "tags": list(item["tags"]),
                    "added_at": int(item.get("added_at", 0) or 0),
                    "size": path.stat().st_size,
                    "available": True,
                    "readonly": True,
                    "source": item["source"],
                    "source_label": item["source_label"],
                    "caption_status": item["caption_status"],
                    "caption_status_label": item["caption_status_label"],
                }
            )
        images.sort(key=lambda item: str(item["filename"]).casefold())
        return {
            "library_id": key,
            "name": (
                self.ORIGINAL_LIBRARY_NAME
                if source is None
                else self.ORIGINAL_SOURCE_NAMES[source]
            ),
            "images": images,
            "readonly": True,
        }

    def _original_image_payload(
        self,
        digest: Any,
        image_id: Any,
        *,
        library_id: Any,
    ) -> dict[str, Any]:
        image_hash = self._hash(digest)
        requested_id = str(image_id or "").strip()
        source = self.original_library_source(library_id)
        for item in self._original_library_items(source):
            if requested_id and item["image_id"] != requested_id:
                continue
            if item["hash"] != image_hash:
                continue
            path = item["path"]
            data = path.read_bytes()
            return {
                "hash": image_hash,
                "image_id": item["image_id"],
                "filename": item["filename"],
                "size": len(data),
                "preview": self._data_url(path.suffix, data),
            }
        raise PolicyConfigError("Smart ImageChat Hub 原图库中不存在该图片。")

    def _original_library_items(
        self,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        target = self._target()
        result = []
        for candidate in self.adapter.original_library_images():
            item_source = str(candidate.get("library_source") or "").strip()
            if source is not None and item_source != source:
                continue
            rel_path = target._norm_rel_path(candidate.get("rel_path"))
            if not rel_path:
                continue
            path = target._abs_plugin_data_path(rel_path)
            if not path.is_file():
                continue
            image_id = str(
                candidate.get("id") or target._image_id(rel_path)
            ).strip()
            digest = str(
                candidate.get("sha256")
                or ""
            ).strip().lower()
            if not self._is_hash(digest):
                digest = self._smart_file_hash(target, path)
            result.append(
                {
                    "hash": digest,
                    "image_id": image_id,
                    "filename": str(
                        candidate.get("filename") or path.name
                    ),
                    "rel_path": rel_path,
                    "path": path,
                    "tags": (
                        self.normalize_tags(candidate.get("tags", []))
                        if candidate.get("tags")
                        else self._source_tags(candidate)
                    ),
                    "source": item_source,
                    "source_label": self.ORIGINAL_SOURCE_LABELS.get(
                        item_source,
                        "手动上传",
                    ),
                    "caption_status": str(
                        candidate.get("caption_status") or ""
                    ).strip(),
                    "caption_status_label": self.CAPTION_STATUS_LABELS.get(
                        str(candidate.get("caption_status") or "").strip(),
                        "未标记状态",
                    ),
                    "added_at": int(
                        candidate.get("updated_at")
                        or candidate.get("captioned_at")
                        or 0
                    ),
                }
            )
        return result

    @staticmethod
    def _smart_index_item(
        target: Any,
        image_id: str,
        rel_path: str,
    ) -> dict[str, Any]:
        getter = getattr(target, "_index_image_by_id", None)
        if callable(getter):
            try:
                item = getter(image_id)
                if isinstance(item, dict):
                    return item
            except Exception:
                pass
        index = getattr(target, "_index", {})
        images = index.get("images", {}) if isinstance(index, dict) else {}
        for item in images.values() if isinstance(images, dict) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "").strip() == image_id:
                return item
            if str(item.get("rel_path") or "").replace("\\", "/") == rel_path:
                return item
        return {}

    @staticmethod
    def _smart_file_hash(target: Any, path: Path) -> str:
        hasher = getattr(target, "_cached_sha256", None)
        if callable(hasher):
            try:
                digest = str(hasher(path) or "").strip().lower()
                if SmartImagePersonaLibraryManager._is_hash(digest):
                    return digest
            except Exception:
                pass
        digest = sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_hash(value: Any) -> bool:
        digest = str(value or "").strip().lower()
        return (
            len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest)
        )

    def _target(self) -> Any:
        target = self.adapter.target or self.adapter._find_target()
        if target is None:
            raise PolicyConfigError("未检测到 Smart ImageChat Hub。")
        compatible, reason = self.adapter._is_compatible(target)
        if not compatible:
            raise PolicyConfigError(reason)
        return target

    def _store_file(self, source: Path) -> tuple[str, str]:
        data = source.read_bytes()
        return self._store_bytes(data, source.suffix)

    def _initial_tags(self, filename: str) -> list[str]:
        target = self.adapter.target or self.adapter._find_target()
        normalizer = getattr(target, "_normalize_caption_tags", None)
        if callable(normalizer):
            try:
                return self.normalize_tags(normalizer([], filename))
            except Exception:
                pass
        stem = Path(filename).stem.strip()
        lowered = filename.casefold()
        image_type = (
            "表情包"
            if any(word in lowered for word in ("meme", "emoji", "表情", "梗图"))
            or Path(filename).suffix.casefold() == ".gif"
            else "照片"
        )
        return self.normalize_tags([image_type, stem] if stem else [image_type])

    def _store_bytes(self, data: bytes, suffix: str) -> tuple[str, str]:
        extension = str(suffix or "").casefold()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            raise PolicyConfigError("仅支持 png、jpg、jpeg、gif、webp 图片。")
        if len(data) > MAX_IMAGE_BYTES:
            raise PolicyConfigError("单张图片不能超过 10 MB。")
        digest = sha256(data).hexdigest()
        existing = self._find_pool_file(digest)
        if existing.is_file():
            return digest, existing.suffix.lstrip(".").lower()
        extension = extension.lstrip(".")
        self.adapter.pool_dir.mkdir(parents=True, exist_ok=True)
        destination = self.adapter.pool_dir / f"{digest}.{extension}"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(destination)
        return digest, extension

    def _find_pool_file(self, digest: str, extension: Any = "") -> Path:
        image_hash = self._hash(digest)
        ext = str(extension or "").strip().lower().lstrip(".")
        if ext:
            candidate = self.adapter.pool_dir / f"{image_hash}.{ext}"
            if candidate.is_file():
                return candidate
        if self.adapter.pool_dir.is_dir():
            matches = sorted(self.adapter.pool_dir.glob(f"{image_hash}.*"))
            if matches:
                return matches[0]
        return self.adapter.pool_dir / f"{image_hash}.{ext or 'jpg'}"

    @staticmethod
    def _hash(value: Any) -> str:
        digest = str(value or "").strip().lower()
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise PolicyConfigError("图片内容哈希无效。")
        return digest

    @classmethod
    def _source_tags(cls, item: dict[str, Any]) -> list[str]:
        for key in ("auto_tags", "tags", "manual_tags"):
            value = item.get(key)
            if isinstance(value, list) and value:
                return cls.normalize_tags(value)
        return []

    def _decode_zip_images(self, data: bytes) -> list[tuple[str, bytes]]:
        result = []
        declared_total = 0
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
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
                    filename = parts[-1]
                    if Path(filename).suffix.casefold() not in ALLOWED_IMAGE_EXTENSIONS:
                        continue
                    declared_total += entry.file_size
                    if entry.file_size > MAX_IMAGE_BYTES:
                        raise PolicyConfigError(
                            f"ZIP 内单张图片不能超过 10 MB：{filename}"
                        )
                    if declared_total > MAX_UPLOAD_TOTAL_BYTES:
                        raise PolicyConfigError(
                            "ZIP 解压后的图片总量不能超过 100 MB。"
                        )
                    result.append((self._safe_filename(filename), archive.read(entry)))
        except PolicyConfigError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise PolicyConfigError("ZIP 压缩包无效或已损坏。") from exc
        return result

    @staticmethod
    def _safe_filename(value: Any) -> str:
        raw = str(value or "").strip()
        if any(char in raw for char in ("/", "\\")):
            raise PolicyConfigError("图片文件名不能包含路径分隔符。")
        filename = Path(raw).name.strip()
        if (
            not filename
            or len(filename) > 200
            or Path(filename).suffix.casefold() not in ALLOWED_IMAGE_EXTENSIONS
        ):
            raise PolicyConfigError("图片文件名无效。")
        return filename

    @classmethod
    def _safe_upload_filename(cls, value: Any) -> str:
        raw = str(value or "").strip()
        if any(char in raw for char in ("/", "\\")):
            raise PolicyConfigError("上传文件名不能包含路径分隔符。")
        filename = Path(raw).name.strip()
        suffix = Path(filename).suffix.casefold()
        if not filename or len(filename) > 200:
            raise PolicyConfigError("上传文件名无效。")
        if suffix != ".zip" and suffix not in ALLOWED_IMAGE_EXTENSIONS:
            raise PolicyConfigError(
                "仅支持 ZIP、png、jpg、jpeg、gif、webp 文件。"
            )
        return filename

    @staticmethod
    def _decode_data_url(value: Any) -> bytes:
        raw = str(value or "")
        if "," in raw and raw.split(",", 1)[0].startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            return base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PolicyConfigError("上传数据不是有效的 base64。") from exc

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
        ):
            raise PolicyConfigError("ZIP 中包含越界路径。")
        return parts

    @staticmethod
    def _zip_entry_is_symlink(entry: zipfile.ZipInfo) -> bool:
        return ((entry.external_attr >> 16) & 0o170000) == 0o120000

    @staticmethod
    def _data_url(suffix: str, data: bytes) -> str:
        mime = {
            ".gif": "image/gif",
            ".jpeg": "image/jpeg",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(str(suffix).casefold(), "application/octet-stream")
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
