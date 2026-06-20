"""Seed the database with realistic dev data matching the frontend mock.

Run once after bootstrap_admin:
    docker compose exec backend uv run python scripts/seed_dev_data.py

Safe to re-run — uses upsert/get-or-create patterns so it won't duplicate rows.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
import app.models_registry  # noqa: F401 — registers all models on Base.metadata
from app.core.security import hash_password
from app.modules.auth.infrastructure.models import User
from app.modules.org.infrastructure.models import (
    ClientCompany,
    Contact,
    Department,
    Parameter,
    Process,
    ProcessStage,
)
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy


# ── helpers ───────────────────────────────────────────────────────────────────


async def get_or_create_param(
    session: AsyncSession, *, type_: str, code: str, name: str
) -> Parameter:
    stmt = select(Parameter).where(Parameter.type == type_, Parameter.code == code)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    param = Parameter(type=type_, code=code, name=name, created_by=1)
    session.add(param)
    await session.flush()
    return param


async def get_or_create_company(session: AsyncSession, name: str) -> ClientCompany:
    stmt = select(ClientCompany).where(ClientCompany.name == name, ClientCompany.is_active.is_(True))
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    company = ClientCompany(name=name, created_by=1)
    session.add(company)
    await session.flush()
    return company


async def get_or_create_department(session: AsyncSession, name: str) -> Department:
    stmt = select(Department).where(Department.name == name, Department.is_active.is_(True))
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    dept = Department(name=name, created_by=1)
    session.add(dept)
    await session.flush()
    return dept


async def get_or_create_contact(
    session: AsyncSession,
    *,
    first_name: str,
    last_name: str,
    email: str,
    company_id: int,
) -> Contact:
    stmt = select(Contact).where(Contact.email == email, Contact.is_active.is_(True))
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    contact = Contact(
        first_name=first_name,
        last_name=last_name,
        email=email,
        client_company_id=company_id,
        created_by=1,
    )
    session.add(contact)
    await session.flush()
    return contact


async def get_or_create_process(
    session: AsyncSession, *, name: str, company_id: int, dept_id: int
) -> Process:
    stmt = select(Process).where(
        Process.name == name,
        Process.client_company_id == company_id,
        Process.is_active.is_(True),
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    process = Process(
        name=name,
        client_company_id=company_id,
        department_id=dept_id,
        created_by=1,
    )
    session.add(process)
    await session.flush()
    return process


# ── seed ──────────────────────────────────────────────────────────────────────


async def seed(session: AsyncSession) -> None:
    print("==> Seeding org.parameters...")

    # Cities
    guayaquil = await get_or_create_param(session, type_="city", code="guayaquil", name="Guayaquil")
    quito = await get_or_create_param(session, type_="city", code="quito", name="Quito")
    await get_or_create_param(session, type_="city", code="cuenca", name="Cuenca")
    await get_or_create_param(session, type_="city", code="ambato", name="Ambato")

    # Work modes
    onsite = await get_or_create_param(session, type_="work_mode", code="onsite", name="Presencial")
    hybrid = await get_or_create_param(session, type_="work_mode", code="hybrid", name="Híbrido")
    remote = await get_or_create_param(session, type_="work_mode", code="remote", name="Remoto")
    project = await get_or_create_param(session, type_="work_mode", code="project", name="Por proyecto")

    # Resource levels
    junior = await get_or_create_param(session, type_="resource_level", code="junior", name="Junior")
    semi_senior = await get_or_create_param(session, type_="resource_level", code="semi_senior", name="Semi Senior")
    senior = await get_or_create_param(session, type_="resource_level", code="senior", name="Senior")
    specialist = await get_or_create_param(session, type_="resource_level", code="specialist", name="Especialista")

    # Vacancy statuses
    active = await get_or_create_param(session, type_="vacancy_status", code="active", name="Activa")
    draft = await get_or_create_param(session, type_="vacancy_status", code="draft", name="Borrador")
    paused = await get_or_create_param(session, type_="vacancy_status", code="paused", name="Pausada")
    await get_or_create_param(session, type_="vacancy_status", code="closed", name="Cerrada")
    await get_or_create_param(session, type_="vacancy_status", code="cancelled", name="Cancelada")

    # Template item categories
    await get_or_create_param(session, type_="template_item_category", code="knowledge", name="Conocimientos")
    await get_or_create_param(session, type_="template_item_category", code="tools", name="Herramientas")
    await get_or_create_param(session, type_="template_item_category", code="skills", name="Habilidades")
    await get_or_create_param(session, type_="template_item_category", code="certifications", name="Certificaciones")

    # Interview statuses
    await get_or_create_param(session, type_="interview_status", code="scheduled", name="Agendada")
    await get_or_create_param(session, type_="interview_status", code="offered", name="Ofrecida")
    await get_or_create_param(session, type_="interview_status", code="confirmed", name="Confirmada")
    await get_or_create_param(session, type_="interview_status", code="cancelled", name="Cancelada")
    await get_or_create_param(session, type_="interview_status", code="completed", name="Completada")

    # Interview schedulers
    await get_or_create_param(session, type_="interview_scheduler", code="hr", name="RH")
    await get_or_create_param(session, type_="interview_scheduler", code="candidate", name="Candidato")

    # Careers
    sistemas = await get_or_create_param(session, type_="career", code="sistemas", name="Ing. en Sistemas / Software")
    industrial = await get_or_create_param(session, type_="career", code="industrial", name="Ing. Industrial")
    economia = await get_or_create_param(session, type_="career", code="economia", name="Economía / Finanzas")
    telecomunicaciones = await get_or_create_param(session, type_="career", code="telecomunicaciones", name="Ing. en Telecomunicaciones")

    # Vacancy names (job titles)
    fullstack = await get_or_create_param(session, type_="vacancy_name", code="fullstack", name="Desarrollador Fullstack")
    qa = await get_or_create_param(session, type_="vacancy_name", code="qa_automatizacion", name="Analista QA Automatización")
    ing_procesos = await get_or_create_param(session, type_="vacancy_name", code="ing_procesos", name="Ing. en Procesos")
    arq_datos = await get_or_create_param(session, type_="vacancy_name", code="arq_datos", name="Arquitecto de Datos")
    devops = await get_or_create_param(session, type_="vacancy_name", code="devops", name="DevOps Engineer")
    scrum = await get_or_create_param(session, type_="vacancy_name", code="scrum_master", name="Scrum Master")
    analista_riesgo = await get_or_create_param(session, type_="vacancy_name", code="analista_riesgo", name="Analista de Riesgo Crediticio")
    soporte = await get_or_create_param(session, type_="vacancy_name", code="ing_soporte", name="Ing. Soporte de Aplicaciones")
    ciberseguridad = await get_or_create_param(session, type_="vacancy_name", code="ciberseguridad", name="Analista de Ciberseguridad")
    product_owner = await get_or_create_param(session, type_="vacancy_name", code="product_owner", name="Product Owner")

    print("==> Seeding org entities (companies, departments, contacts, processes)...")

    # Client companies
    bg = await get_or_create_company(session, "Banco Guayaquil")
    bp = await get_or_create_company(session, "Banco del Pacífico")
    produ = await get_or_create_company(session, "Produbanco")
    diners = await get_or_create_company(session, "Diners Club")
    pichincha = await get_or_create_company(session, "Banco Pichincha")
    claro = await get_or_create_company(session, "Claro Ecuador")
    cnt = await get_or_create_company(session, "CNT EP")
    favorita = await get_or_create_company(session, "Corp. Favorita")

    # Departments
    tech = await get_or_create_department(session, "Tecnología")
    calidad = await get_or_create_department(session, "Calidad")
    procesos_dept = await get_or_create_department(session, "Procesos")
    datos = await get_or_create_department(session, "Datos & Analytics")
    infra = await get_or_create_department(session, "Infraestructura")
    riesgos = await get_or_create_department(session, "Riesgos")
    ops_ti = await get_or_create_department(session, "Operaciones TI")
    seguridad = await get_or_create_department(session, "Seguridad")
    producto = await get_or_create_department(session, "Producto")

    # Contacts
    monica = await get_or_create_contact(session, first_name="Mónica", last_name="Andrade", email="m.andrade@bancog.com", company_id=bg.id)
    ricardo = await get_or_create_contact(session, first_name="Ricardo", last_name="Mora", email="r.mora@bancop.com", company_id=bp.id)
    lucia = await get_or_create_contact(session, first_name="Lucía", last_name="Herrera", email="l.herrera@produbanco.com", company_id=produ.id)
    pablo = await get_or_create_contact(session, first_name="Pablo", last_name="Vásquez", email="p.vasquez@diners.com", company_id=diners.id)
    fernando = await get_or_create_contact(session, first_name="Fernando", last_name="Paredes", email="f.paredes@pichincha.com", company_id=pichincha.id)
    gabriela = await get_or_create_contact(session, first_name="Gabriela", last_name="Saltos", email="g.saltos@claro.com.ec", company_id=claro.id)
    andres = await get_or_create_contact(session, first_name="Andrés", last_name="Villacís", email="a.villacis@cnt.gob.ec", company_id=cnt.id)
    mateo = await get_or_create_contact(session, first_name="Mateo", last_name="Torres", email="m.torres@favorita.com", company_id=favorita.id)

    # Processes
    proc_bg_tech = await get_or_create_process(session, name="BG · Tecnología — Estándar 5 etapas", company_id=bg.id, dept_id=tech.id)
    proc_bp_qa = await get_or_create_process(session, name="BP · Calidad — QA 4 etapas", company_id=bp.id, dept_id=calidad.id)
    proc_generic = await get_or_create_process(session, name="Genérico — 3 etapas", company_id=produ.id, dept_id=tech.id)

    print("==> Seeding recruitment.vacancies...")

    async def vacancy_exists(session: AsyncSession, company_id: int, name_id: int) -> bool:
        stmt = select(Vacancy).where(
            Vacancy.client_company_id == company_id,
            Vacancy.vacancy_name_id == name_id,
            Vacancy.is_active.is_(True),
        )
        return (await session.execute(stmt)).scalar_one_or_none() is not None

    vacancies_data = [
        dict(
            vacancy_name_id=fullstack.id, client_company_id=bg.id, contact_id=monica.id,
            department_id=tech.id, process_id=proc_bg_tech.id,
            career_id=sistemas.id, city_id=guayaquil.id, work_mode_id=hybrid.id,
            resource_level_id=senior.id, status_id=active.id,
            openings=2, experience_years=3,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=2, project_duration_months=0,
            description="Responsable del desarrollo y mantenimiento de aplicaciones bancarias sobre Angular y .NET 8.",
            profile_requirements={
                "knowledge": ["C# / .NET 8", "Angular 17", "TypeScript", "SQL Server", "REST API"],
                "tools": ["Git", "Azure DevOps", "Postman", "Docker"],
                "skills": ["Comunicación efectiva", "Trabajo en equipo", "Resolución de problemas"],
                "certifications": ["Microsoft Certified: Azure Developer (deseable)"],
            },
            created_by=1,
        ),
        dict(
            vacancy_name_id=qa.id, client_company_id=bp.id, contact_id=ricardo.id,
            department_id=calidad.id, process_id=proc_bp_qa.id,
            career_id=sistemas.id, city_id=guayaquil.id, work_mode_id=onsite.id,
            resource_level_id=semi_senior.id, status_id=active.id,
            openings=3, experience_years=2,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=1, project_duration_months=0,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
        dict(
            vacancy_name_id=ing_procesos.id, client_company_id=produ.id, contact_id=lucia.id,
            department_id=procesos_dept.id, process_id=proc_generic.id,
            career_id=industrial.id, city_id=quito.id, work_mode_id=hybrid.id,
            resource_level_id=senior.id, status_id=active.id,
            openings=1, experience_years=4,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=1, project_duration_months=6,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
        dict(
            vacancy_name_id=arq_datos.id, client_company_id=bg.id, contact_id=monica.id,
            department_id=datos.id, process_id=proc_bg_tech.id,
            career_id=sistemas.id, city_id=guayaquil.id, work_mode_id=remote.id,
            resource_level_id=specialist.id, status_id=paused.id,
            openings=1, experience_years=5,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=0, project_duration_months=6,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
        dict(
            vacancy_name_id=devops.id, client_company_id=diners.id, contact_id=pablo.id,
            department_id=infra.id, process_id=proc_generic.id,
            career_id=sistemas.id, city_id=quito.id, work_mode_id=hybrid.id,
            resource_level_id=senior.id, status_id=draft.id,
            openings=1, experience_years=4,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=0, project_duration_months=0,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
        dict(
            vacancy_name_id=scrum.id, client_company_id=bp.id, contact_id=ricardo.id,
            department_id=tech.id, process_id=proc_bp_qa.id,
            career_id=sistemas.id, city_id=guayaquil.id, work_mode_id=remote.id,
            resource_level_id=senior.id, status_id=active.id,
            openings=1, experience_years=3,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=0, project_duration_months=8,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
        dict(
            vacancy_name_id=analista_riesgo.id, client_company_id=pichincha.id, contact_id=fernando.id,
            department_id=riesgos.id, process_id=proc_generic.id,
            career_id=economia.id, city_id=quito.id, work_mode_id=onsite.id,
            resource_level_id=junior.id, status_id=active.id,
            openings=4, experience_years=0,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=0, project_duration_months=8,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
        dict(
            vacancy_name_id=soporte.id, client_company_id=claro.id, contact_id=gabriela.id,
            department_id=ops_ti.id, process_id=proc_generic.id,
            career_id=sistemas.id, city_id=guayaquil.id, work_mode_id=onsite.id,
            resource_level_id=semi_senior.id, status_id=active.id,
            openings=2, experience_years=2,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=1, project_duration_months=0,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
        dict(
            vacancy_name_id=ciberseguridad.id, client_company_id=cnt.id, contact_id=andres.id,
            department_id=seguridad.id, process_id=proc_generic.id,
            career_id=telecomunicaciones.id, city_id=quito.id, work_mode_id=hybrid.id,
            resource_level_id=senior.id, status_id=draft.id,
            openings=1, experience_years=3,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=0, project_duration_months=0,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
        dict(
            vacancy_name_id=product_owner.id, client_company_id=favorita.id, contact_id=mateo.id,
            department_id=producto.id, process_id=proc_generic.id,
            career_id=industrial.id, city_id=quito.id, work_mode_id=project.id,
            resource_level_id=specialist.id, status_id=active.id,
            openings=1, experience_years=5,
            work_schedule="Lunes a viernes, 8:00–17:00",
            project_duration_years=0, project_duration_months=0,
            description="",
            profile_requirements={"knowledge": [], "tools": [], "skills": [], "certifications": []},
            created_by=1,
        ),
    ]

    created = 0
    for data in vacancies_data:
        if not await vacancy_exists(session, data["client_company_id"], data["vacancy_name_id"]):
            session.add(Vacancy(**data))
            created += 1

    await session.flush()
    print(f"   {created} new vacancies created ({len(vacancies_data) - created} already existed)")

    await seed_pipeline_stages(session, proc_bg_tech.id, proc_bp_qa.id, proc_generic.id)
    await seed_candidates_and_applications(session)


async def get_or_create_process_stage(
    session: AsyncSession, *, process_id: int, stage_id: int, order: int, is_final: bool = False
) -> ProcessStage:
    stmt = select(ProcessStage).where(
        ProcessStage.process_id == process_id,
        ProcessStage.stage_id == stage_id,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    ps = ProcessStage(
        process_id=process_id,
        stage_id=stage_id,
        order=order,
        is_final_positive=is_final,
        created_by=1,
    )
    session.add(ps)
    await session.flush()
    return ps


async def seed_pipeline_stages(
    session: AsyncSession, proc_bg_tech_id: int, proc_bp_qa_id: int, proc_generic_id: int
) -> None:
    print("==> Seeding process stages...")

    # Stage name parameters
    cv = await get_or_create_param(session, type_="stage", code="cv_received", name="CV recibido")
    llamada = await get_or_create_param(session, type_="stage", code="validation_call", name="Llamada de validación")
    prueba = await get_or_create_param(session, type_="stage", code="technical_test", name="Prueba técnica")
    entrevista_hr = await get_or_create_param(session, type_="stage", code="hr_interview", name="Entrevista HR")
    entrevista_cli = await get_or_create_param(session, type_="stage", code="client_interview", name="Entrevista cliente")
    oferta = await get_or_create_param(session, type_="stage", code="offer", name="Oferta · Contratación")
    entrevista_gen = await get_or_create_param(session, type_="stage", code="interview", name="Entrevista")

    # BG · Tecnología — 5 etapas
    await get_or_create_process_stage(session, process_id=proc_bg_tech_id, stage_id=cv.id, order=1)
    await get_or_create_process_stage(session, process_id=proc_bg_tech_id, stage_id=llamada.id, order=2)
    await get_or_create_process_stage(session, process_id=proc_bg_tech_id, stage_id=prueba.id, order=3)
    await get_or_create_process_stage(session, process_id=proc_bg_tech_id, stage_id=entrevista_cli.id, order=4)
    await get_or_create_process_stage(session, process_id=proc_bg_tech_id, stage_id=oferta.id, order=5, is_final=True)

    # BP · Calidad — 4 etapas
    await get_or_create_process_stage(session, process_id=proc_bp_qa_id, stage_id=cv.id, order=1)
    await get_or_create_process_stage(session, process_id=proc_bp_qa_id, stage_id=entrevista_hr.id, order=2)
    await get_or_create_process_stage(session, process_id=proc_bp_qa_id, stage_id=prueba.id, order=3)
    await get_or_create_process_stage(session, process_id=proc_bp_qa_id, stage_id=oferta.id, order=4, is_final=True)

    # Genérico — 3 etapas
    await get_or_create_process_stage(session, process_id=proc_generic_id, stage_id=cv.id, order=1)
    await get_or_create_process_stage(session, process_id=proc_generic_id, stage_id=entrevista_gen.id, order=2)
    await get_or_create_process_stage(session, process_id=proc_generic_id, stage_id=oferta.id, order=3, is_final=True)

    print("   Process stages seeded.")


async def get_or_create_candidate_user(session: AsyncSession, email: str, portal_id: int) -> User:
    stmt = select(User).where(User.email == email)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    user = User(
        email=email,
        password_hash=hash_password("Candidato123!"),
        portal_id=portal_id,
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def seed_candidates_and_applications(session: AsyncSession) -> None:
    print("==> Seeding candidates and applications...")

    candidate_portal = await ParameterRepository(session).get_by_type_and_code(
        "user_portal", "candidate"
    )
    if candidate_portal is None:
        print("   WARNING: candidate portal parameter not found, skipping candidates.")
        return

    # app_status parameter (needed for Application.status_id)
    app_status = await get_or_create_param(session, type_="application_status", code="active", name="Activa")

    # Get first vacancy (Fullstack BG) for sample applications
    vacancy_stmt = select(Vacancy).where(Vacancy.is_active.is_(True)).order_by(Vacancy.id).limit(1)
    first_vacancy = (await session.execute(vacancy_stmt)).scalar_one_or_none()

    # Get first process stage of first vacancy's process (for placing cards)
    first_stage = None
    second_stage = None
    if first_vacancy:
        stages_stmt = (
            select(ProcessStage)
            .where(ProcessStage.process_id == first_vacancy.process_id)
            .where(ProcessStage.is_active.is_(True))
            .order_by(ProcessStage.order)
            .limit(3)
        )
        stages = list((await session.execute(stages_stmt)).scalars().all())
        if stages:
            first_stage = stages[0]
        if len(stages) > 1:
            second_stage = stages[1]

    # Parameters for candidates
    guayaquil_p = (await session.execute(
        select(Parameter).where(Parameter.type == "city", Parameter.code == "guayaquil")
    )).scalar_one_or_none()
    quito_p = (await session.execute(
        select(Parameter).where(Parameter.type == "city", Parameter.code == "quito")
    )).scalar_one_or_none()
    sistemas_p = (await session.execute(
        select(Parameter).where(Parameter.type == "career", Parameter.code == "sistemas")
    )).scalar_one_or_none()
    economia_p = (await session.execute(
        select(Parameter).where(Parameter.type == "career", Parameter.code == "economia")
    )).scalar_one_or_none()
    telecom_p = (await session.execute(
        select(Parameter).where(Parameter.type == "career", Parameter.code == "telecomunicaciones")
    )).scalar_one_or_none()

    candidates_data = [
        dict(email="santiago.almeida@gmail.com", first_name="Santiago", last_name="Almeida Paredes",
             cedula="1723456789", phone="+593983124567", city_id=quito_p.id if quito_p else None,
             career_id=sistemas_p.id if sistemas_p else None, is_studying=False, is_working=True,
             current_company="TechSolutions S.A."),
        dict(email="valeria.mosquera@outlook.com", first_name="Valeria", last_name="Mosquera Cifuentes",
             cedula="0912345678", phone="+593994567890", city_id=guayaquil_p.id if guayaquil_p else None,
             career_id=sistemas_p.id if sistemas_p else None, is_studying=False, is_working=False,
             current_company=None),
        dict(email="j.intriago@hotmail.com", first_name="Jorge", last_name="Intriago Loor",
             cedula="1312345670", phone="+593967890123", city_id=None,
             career_id=telecom_p.id if telecom_p else None, is_studying=True, is_working=True,
             current_company="Freelance"),
        dict(email="andrea.cevallos@gmail.com", first_name="Andrea", last_name="Cevallos Espín",
             cedula="1756789012", phone="+593991234567", city_id=quito_p.id if quito_p else None,
             career_id=sistemas_p.id if sistemas_p else None, is_studying=False, is_working=True,
             current_company="Banco Central del Ecuador"),
        dict(email="r.delgado.dev@gmail.com", first_name="Ramiro", last_name="Delgado Vega",
             cedula="0934567890", phone="+593980123456", city_id=guayaquil_p.id if guayaquil_p else None,
             career_id=sistemas_p.id if sistemas_p else None, is_studying=False, is_working=False,
             current_company=None),
        dict(email="sofia.herrera.ec@gmail.com", first_name="Sofía", last_name="Herrera Narváez",
             cedula="1778901234", phone="+593996789012", city_id=quito_p.id if quito_p else None,
             career_id=economia_p.id if economia_p else None, is_studying=True, is_working=False,
             current_company=None),
        dict(email="dmorales.tech@outlook.com", first_name="Diego", last_name="Morales Castillo",
             cedula="0956789012", phone="+593974567890", city_id=guayaquil_p.id if guayaquil_p else None,
             career_id=sistemas_p.id if sistemas_p else None, is_studying=False, is_working=True,
             current_company="Corporación FAVORITA"),
        dict(email="catalina.ruiz.rec@gmail.com", first_name="Catalina", last_name="Ruiz Benalcázar",
             cedula="1789012345", phone="+593993456789", city_id=quito_p.id if quito_p else None,
             career_id=economia_p.id if economia_p else None, is_studying=False, is_working=True,
             current_company="Superintendencia de Bancos"),
    ]

    new_candidates = []
    created_c = 0
    for cd in candidates_data:
        email = cd.pop("email")
        user = await get_or_create_candidate_user(session, email, candidate_portal.id)
        # Check if candidate already exists for this user
        existing = (await session.execute(
            select(Candidate).where(Candidate.user_id == user.id)
        )).scalar_one_or_none()
        if existing:
            new_candidates.append(existing)
            continue
        candidate = Candidate(user_id=user.id, created_by=1, **cd)
        session.add(candidate)
        await session.flush()
        new_candidates.append(candidate)
        created_c += 1

    print(f"   {created_c} new candidates created ({len(candidates_data) - created_c} already existed)")

    # Applications: place first 5 candidates into the first vacancy pipeline
    created_a = 0
    if first_vacancy and first_stage:
        stage_assignments = [
            first_stage.id, first_stage.id, second_stage.id if second_stage else first_stage.id,
            second_stage.id if second_stage else first_stage.id, first_stage.id,
        ]
        for i, candidate in enumerate(new_candidates[:5]):
            stmt = select(Application).where(
                Application.vacancy_id == first_vacancy.id,
                Application.candidate_id == candidate.id,
            )
            if (await session.execute(stmt)).scalar_one_or_none():
                continue
            app = Application(
                vacancy_id=first_vacancy.id,
                candidate_id=candidate.id,
                current_stage_id=stage_assignments[i],
                status_id=app_status.id,
                created_by=1,
            )
            session.add(app)
            created_a += 1

        await session.flush()
        print(f"   {created_a} new applications created for vacancy {first_vacancy.id}")


async def main() -> None:
    async with async_session_factory() as session:
        try:
            await seed(session)
            await session.commit()
            print("==> Seed complete.")
        except Exception as exc:
            await session.rollback()
            print(f"ERROR: {exc}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
