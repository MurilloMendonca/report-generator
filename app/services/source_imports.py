"""Import immutable snapshots before entering the existing report pipeline."""
import io
import uuid

from fastapi import UploadFile

from app.services import quotas, source_service, session_manager
from app.services.cleanup_service import safe_remove_path
from app.services.pipeline_service import run_pipeline, serialized_pipeline
from app.services.session_manager import preserve_report_on_failure
from app.services.source_errors import SourceError
from app.services.web_helpers import save_uploaded_files
from app.utils.report_paths import ReportPaths


def import_saved_sources(job_id: str, token: str):
    with serialized_pipeline(job_id, "source_import"):
        _import_saved_sources(job_id, token)


@preserve_report_on_failure
def _import_saved_sources(job_id: str, token: str):
    operation = session_manager.begin_source_import(job_id, token)
    if not operation:
        return
    staged, accepted, incoming = [], False, 0
    try:
        session = session_manager.get_session(job_id)
        owner_id = session_manager.get_report_owner(job_id)
        paths = ReportPaths.from_session(session).ensure()
        for reference in operation["sources"]:
            body, source = source_service.read_dataset(owner_id, reference)
            incoming += len(body)
            quotas.check_storage(owner_id, incoming)
            source["snapshot_id"] = uuid.uuid4().hex
            source["import_id"] = token
            filename = f"dataset_{uuid.UUID(reference['dataset_id']).hex}.json"
            upload = UploadFile(filename=filename, file=io.BytesIO(body))
            try:
                saved = save_uploaded_files([upload], paths.uploads)
            finally:
                upload.file.close()
            saved[0]["source"] = source
            staged.extend(saved)
        if not session_manager.commit_source_import(job_id, token, staged):
            raise SourceError("source_not_found", "O relatório não está mais disponível.", 404)
        accepted = True
        quotas.refresh_storage(job_id)
        session_manager.add_log(job_id, f"Importadas {len(staged)} fontes completas. Snapshots anteriores foram preservados.")
        # Already inside run_job: do not schedule a nested job.
        run_pipeline(job_id)
    except SourceError as exc:
        session_manager.fail_source_import(job_id, token, exc.as_error_dict())
    except Exception:
        session_manager.fail_source_import(job_id, token, SourceError(
            "source_import_error", "Não foi possível importar as fontes. Nenhum snapshot parcial foi aceito."
        ).as_error_dict())
    finally:
        if not accepted:
            for item in staged:
                safe_remove_path(item["path"])
