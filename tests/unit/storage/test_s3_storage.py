"""Unit tests for the S3 storage backend (mocked with moto).

Covers the CRUD surface, whole-object streaming and the ranged-GET
random-access reader used by HTTP Range serving and chunked-envelope
decryption.
"""
import io

import pytest

from app.infrastructure.storage.base import (
    FileNotFoundError as StorageFileNotFoundError,
    StorageConfig,
)
from app.infrastructure.storage.factory import (
    get_storage,
    get_storage_from_config,
    reset_storage,
)
from app.infrastructure.storage.local_storage import LocalStorage
from app.infrastructure.storage.s3_storage import S3RandomAccessReader, S3Storage

moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")

BUCKET = "synth-gallery-test"


@pytest.fixture
def s3_storage():
    """S3Storage backed by a mocked S3 endpoint."""
    with moto.mock_aws():
        config = StorageConfig(
            backend="s3",
            bucket_name=BUCKET,
            access_key="test-access",
            secret_key="test-secret",
            region="us-east-1",
            use_ssl=False,
        )
        yield S3Storage(config)


class TestS3Crud:
    """Basic object operations."""

    @pytest.mark.asyncio
    async def test_upload_download_roundtrip(self, s3_storage):
        await s3_storage.upload("file1", b"hello world", "uploads")
        data = await s3_storage.download("file1", "uploads")
        assert data == b"hello world"

    @pytest.mark.asyncio
    async def test_download_missing_raises(self, s3_storage):
        with pytest.raises(StorageFileNotFoundError):
            await s3_storage.download("missing", "uploads")

    @pytest.mark.asyncio
    async def test_exists(self, s3_storage):
        assert not s3_storage.exists("file1", "uploads")
        await s3_storage.upload("file1", b"x", "uploads")
        assert s3_storage.exists("file1", "uploads")

    @pytest.mark.asyncio
    async def test_get_size(self, s3_storage):
        await s3_storage.upload("file1", b"12345678", "uploads")
        assert await s3_storage.get_size("file1", "uploads") == 8

    @pytest.mark.asyncio
    async def test_delete(self, s3_storage):
        await s3_storage.upload("file1", b"x", "uploads")
        assert await s3_storage.delete("file1", "uploads") is True
        assert not s3_storage.exists("file1", "uploads")

    @pytest.mark.asyncio
    async def test_copy_and_move(self, s3_storage):
        await s3_storage.upload("src", b"data", "uploads")
        await s3_storage.copy("src", "dst", "uploads", "thumbnails")
        assert await s3_storage.download("dst", "thumbnails") == b"data"

        await s3_storage.move("src", "moved", "uploads", "uploads")
        assert await s3_storage.download("moved", "uploads") == b"data"
        assert not s3_storage.exists("src", "uploads")

    @pytest.mark.asyncio
    async def test_list_files(self, s3_storage):
        await s3_storage.upload("a", b"1", "uploads")
        await s3_storage.upload("b", b"2", "uploads")
        await s3_storage.upload("c", b"3", "thumbnails")
        assert sorted(s3_storage.list_files("uploads")) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_get_stream_reads_body(self, s3_storage):
        await s3_storage.upload("file1", b"streamed", "uploads")
        stream = await s3_storage.get_stream("file1", "uploads")
        assert stream.read() == b"streamed"


class TestS3RandomAccessReader:
    """The ranged-GET reader semantics."""

    @pytest.fixture
    def seeded(self, s3_storage):
        """Upload deterministic content spanning multiple windows."""
        self.content = bytes(range(256)) * 1024  # 256 KiB
        self.size = len(self.content)

        import asyncio

        asyncio.run(s3_storage.upload("obj", self.content, "uploads"))
        return s3_storage

    def _reader(self, storage) -> S3RandomAccessReader:
        return storage.get_random_access_reader("obj", "uploads")

    def test_read_all(self, seeded):
        with self._reader(seeded) as reader:
            assert reader.size == self.size
            assert reader.read() == self.content

    def test_sequential_small_reads(self, seeded):
        with self._reader(seeded) as reader:
            out = b""
            while True:
                chunk = reader.read(100)
                if not chunk:
                    break
                out += chunk
            assert out == self.content

    def test_read_exact_span_beyond_window(self, seeded):
        with self._reader(seeded) as reader:
            assert reader.read(self.size) == self.content

    def test_seek_and_read(self, seeded):
        with self._reader(seeded) as reader:
            assert reader.seek(1000) == 1000
            assert reader.tell() == 1000
            assert reader.read(10) == self.content[1000:1010]

    def test_seek_backwards_inside_window(self, seeded):
        with self._reader(seeded) as reader:
            reader.read(50)          # fills the read-ahead window
            reader.seek(10)          # backward, still inside the window
            assert reader.read(5) == self.content[10:15]
            reader.seek(20)          # forward inside the window
            assert reader.read(5) == self.content[20:25]

    def test_seek_whence_cur_and_end(self, seeded):
        with self._reader(seeded) as reader:
            reader.seek(100)
            reader.seek(10, io.SEEK_CUR)
            assert reader.tell() == 110
            reader.seek(-10, io.SEEK_END)
            assert reader.tell() == self.size - 10
            assert reader.read() == self.content[-10:]

    def test_read_at_eof_returns_empty(self, seeded):
        with self._reader(seeded) as reader:
            assert reader.seek(self.size) == self.size
            assert reader.read(100) == b""
            assert reader.read() == b""

    def test_seek_past_eof_read_returns_empty(self, seeded):
        with self._reader(seeded) as reader:
            reader.seek(self.size + 500)
            assert reader.read() == b""

    def test_partial_read_then_continue(self, seeded):
        with self._reader(seeded) as reader:
            first = reader.read(70_000)      # larger than one window
            second = reader.read(70_000)
            assert first == self.content[:70_000]
            assert second == self.content[70_000:140_000]

    def test_tail_read(self, seeded):
        with self._reader(seeded) as reader:
            reader.seek(self.size - 3)
            assert reader.read(10) == self.content[-3:]

    def test_read_zero_bytes(self, seeded):
        with self._reader(seeded) as reader:
            assert reader.read(0) == b""

    def test_missing_object_raises(self, s3_storage):
        with pytest.raises(StorageFileNotFoundError):
            s3_storage.get_random_access_reader("nope", "uploads")


class TestStorageFactory:
    """Backend selection from environment variables."""

    def setup_method(self):
        reset_storage()

    def teardown_method(self):
        reset_storage()

    def test_default_is_local(self, monkeypatch):
        monkeypatch.delenv("STORAGE_BACKEND", raising=False)
        storage = get_storage()
        assert isinstance(storage, LocalStorage)

    def test_s3_backend_selected(self, monkeypatch):
        with moto.mock_aws():
            monkeypatch.setenv("STORAGE_BACKEND", "s3")
            monkeypatch.setenv("S3_BUCKET", BUCKET)
            monkeypatch.setenv("S3_ACCESS_KEY", "k")
            monkeypatch.setenv("S3_SECRET_KEY", "s")
            reset_storage()
            storage = get_storage()
            assert isinstance(storage, S3Storage)

    def test_s3_without_bucket_raises(self, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "s3")
        monkeypatch.delenv("S3_BUCKET", raising=False)
        from app.infrastructure.storage.factory import get_storage_config

        with pytest.raises(ValueError, match="S3_BUCKET"):
            get_storage_config()

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown storage backend"):
            get_storage_from_config(StorageConfig(backend="ftp"))
