from app.core.dependencies import CurrentUser
from app.modules.ai.api.cv_parse_jobs_schemas import CvParseJobCreate, CvParseJobUpdate
from app.modules.ai.infrastructure.models import CvParseJob
from app.modules.org.infrastructure.models import Parameter
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.storage.infrastructure.models import File
from app.shared.pagination import PageParams
from app.shared.repository import BaseRepository


class CvParseJobNotFoundError(Exception):
    pass


class CvParseJobReferenceError(Exception):
    pass


class CvParseJobService:
    def __init__(
        self,
        repository: BaseRepository[CvParseJob],
        files: BaseRepository[File],
        candidates: BaseRepository[Candidate],
        parameters: BaseRepository[Parameter],
    ) -> None:
        self.repository = repository
        self.files = files
        self.candidates = candidates
        self.parameters = parameters

    async def list(
        self,
        params: PageParams,
        *,
        candidate_id: int | None = None,
        status_id: int | None = None,
    ) -> tuple[list[CvParseJob], int]:
        filters = {
            k: v
            for k, v in {"candidate_id": candidate_id, "status_id": status_id}.items()
            if v is not None
        }
        return await self.repository.list(params, filters=filters or None)

    async def get(self, job_id: int) -> CvParseJob:
        job = await self.repository.get(job_id)
        if job is None:
            raise CvParseJobNotFoundError(f"CV parse job {job_id} not found")
        return job

    async def create(self, data: CvParseJobCreate, actor: CurrentUser) -> CvParseJob:
        if await self.files.get(data.file_id) is None:
            raise CvParseJobReferenceError(f"file_id={data.file_id} not found")
        if await self.candidates.get(data.candidate_id) is None:
            raise CvParseJobReferenceError(f"candidate_id={data.candidate_id} not found")
        if await self.parameters.get(data.status_id) is None:
            raise CvParseJobReferenceError(f"status_id={data.status_id} not found")
        job = CvParseJob(
            **data.model_dump(),
            created_by=actor.user_id,
            ip_created=actor.ip,
        )
        return await self.repository.add(job)

    async def update(
        self, job_id: int, data: CvParseJobUpdate, actor: CurrentUser
    ) -> CvParseJob:
        job = await self.get(job_id)
        changes = data.model_dump(exclude_unset=True)
        if "status_id" in changes and await self.parameters.get(changes["status_id"]) is None:
            raise CvParseJobReferenceError(f"status_id={changes['status_id']} not found")
        changes["updated_by"] = actor.user_id
        changes["ip_updated"] = actor.ip
        return await self.repository.update(job, changes)

    async def delete(self, job_id: int) -> None:
        job = await self.get(job_id)
        await self.repository.soft_delete(job)
