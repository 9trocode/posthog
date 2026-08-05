import json
import errno
from typing import cast

import pytest
from unittest.mock import MagicMock, patch

import pyarrow as pa
import structlog
from structlog.types import FilteringBoundLogger

from products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline_v3.s3.writer import (
    S3BatchWriter,
    _write_parquet_to_s3,
    build_schema_dict,
)


def _fake_s3(open_side_effect: list) -> MagicMock:
    s3 = MagicMock()
    s3.open.return_value.__enter__.side_effect = open_side_effect
    # A MagicMock's auto-mocked __exit__ returns a truthy MagicMock by default, which would
    # suppress the exception pyarrow.parquet.write_table raises inside the `with` block.
    s3.open.return_value.__exit__.return_value = False
    return s3


class TestWriteParquetToS3:
    @patch("tenacity.nap.time.sleep")
    @patch("pyarrow.parquet.write_table")
    def test_retries_transient_os_error_then_succeeds(self, mock_write_table, _mock_sleep) -> None:
        # s3fs translates a truncated-upload S3 response (e.g. IncompleteBody) into a plain
        # OSError; a single dropped connection shouldn't fail the whole batch write.
        f = MagicMock()
        s3 = _fake_s3([f, f])
        mock_write_table.side_effect = [OSError(errno.EINVAL, "The request body terminated unexpectedly"), None]

        _write_parquet_to_s3(s3, "bucket/part-0000.parquet", pa.table({"id": [1]}), "zstd")

        assert mock_write_table.call_count == 2

    @patch("tenacity.nap.time.sleep")
    @patch("pyarrow.parquet.write_table")
    def test_reraises_after_persistent_os_error(self, mock_write_table, _mock_sleep) -> None:
        f = MagicMock()
        s3 = _fake_s3([f, f, f, f])
        mock_write_table.side_effect = OSError(errno.EINVAL, "The request body terminated unexpectedly")

        with pytest.raises(OSError):
            _write_parquet_to_s3(s3, "bucket/part-0000.parquet", pa.table({"id": [1]}), "zstd")

        assert mock_write_table.call_count == 4

    @patch("pyarrow.parquet.write_table")
    def test_does_not_retry_permission_error(self, mock_write_table) -> None:
        # PermissionError (e.g. AccessDenied/InvalidAccessKeyId) is an OSError subclass but not
        # transient — retrying it just delays a failure that will happen on every attempt.
        f = MagicMock()
        s3 = _fake_s3([f])
        mock_write_table.side_effect = PermissionError("Access Denied")

        with pytest.raises(PermissionError):
            _write_parquet_to_s3(s3, "bucket/part-0000.parquet", pa.table({"id": [1]}), "zstd")

        assert mock_write_table.call_count == 1


class TestBuildSchemaDict:
    def test_field_metadata_is_json_serializable(self) -> None:
        schema = pa.schema([pa.field("id", pa.int64(), metadata={"comment": "primary key"})])

        schema_dict = build_schema_dict(schema)

        # Would raise "keys must be str ... not bytes" if the bytes metadata wasn't decoded.
        json.dumps(schema_dict)
        assert schema_dict["fields"][0]["metadata"] == {"comment": "primary key"}

    def test_field_without_metadata_stays_none(self) -> None:
        schema = pa.schema([pa.field("id", pa.int64())])

        assert build_schema_dict(schema)["fields"][0]["metadata"] is None


class TestWriteBatchSchemaAccumulation:
    def test_conflicting_column_types_across_batches_do_not_fail_the_write(self) -> None:
        # A vendor sending the same id as int in one page and string in the next used to abort
        # the whole table's sync with ArrowTypeError when the batch schemas were folded together.
        job = MagicMock(team_id=1, created_at=None)
        with (
            patch(
                "products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline_v3.s3.writer.get_date_partition",
                return_value="2026-07-30",
            ),
            patch(
                "products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline_v3.s3.writer.ensure_bucket"
            ),
            patch(
                "products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline_v3.s3.writer.get_s3_client"
            ),
            patch(
                "products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline_v3.s3.writer.pq.write_table"
            ),
        ):
            writer = S3BatchWriter(
                cast(FilteringBoundLogger, structlog.get_logger()), job, schema_id="s1", run_uuid="run-1"
            )

            writer.write_batch(pa.table({"owner_id": pa.array([1], type=pa.int64())}), batch_index=0)
            writer.write_batch(pa.table({"owner_id": pa.array(["2"], type=pa.string())}), batch_index=1)

        schema = writer.get_schema()
        assert schema is not None
        assert schema.field("owner_id").type == pa.string()
