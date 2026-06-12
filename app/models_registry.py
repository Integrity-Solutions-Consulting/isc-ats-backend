"""Central import point so Base.metadata is fully populated.

Alembic's autogenerate and any metadata.create_all need every model module
imported at least once. Add each module's models here as they are built.
"""

from app.modules.auth.infrastructure import models as auth_models  # noqa: F401
from app.modules.org.infrastructure import models as org_models  # noqa: F401
from app.modules.recruitment.infrastructure import models as recruitment_models  # noqa: F401
from app.modules.storage.infrastructure import models as storage_models  # noqa: F401
from app.modules.ai.infrastructure import models as ai_models  # noqa: F401
from app.modules.comms.infrastructure import models as comms_models  # noqa: F401
from app.modules.talent.infrastructure import models as talent_models  # noqa: F401
