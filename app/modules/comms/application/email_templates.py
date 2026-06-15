# ruff: noqa: E501 - inline HTML email markup intentionally exceeds the line length
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

# Interview times are stored in UTC; show them in Ecuador local time to candidates.
# Ecuador is a fixed UTC-5 (no daylight saving), so a fixed offset is correct and
# avoids depending on the IANA tz database (absent on Windows / slim images).
_EC_TZ = timezone(timedelta(hours=-5))

# Brand color — kept inline because email clients strip <style> and external CSS.
_PRIMARY = "#1d4ed8"


@dataclass(frozen=True)
class RenderedEmail:
    """A fully rendered, localized email ready to be sent."""

    subject: str
    html_body: str
    text_body: str


def render_verification_email(verification_url: str) -> RenderedEmail:
    """Account-verification email (Spanish, Ecuador).

    `verification_url` is the full link the candidate clicks to activate the
    account; it embeds the verification token as a query parameter.
    """
    subject = "Verifica tu cuenta — Integrity Solutions"

    text_body = (
        "¡Bienvenido a Integrity Solutions!\n\n"
        "Para activar tu cuenta y comenzar a explorar vacantes, abre este enlace:\n"
        f"{verification_url}\n\n"
        "El enlace vence en 24 horas. Si no creaste esta cuenta, ignora este correo."
    )

    html_body = f"""\
<!DOCTYPE html>
<html lang="es">
  <body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="520" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
            <tr>
              <td style="background:{_PRIMARY};padding:24px 32px;color:#ffffff;font-size:18px;font-weight:bold;">
                Integrity Solutions
              </td>
            </tr>
            <tr>
              <td style="padding:32px;color:#111827;">
                <h1 style="margin:0 0 12px;font-size:22px;">Verifica tu cuenta</h1>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.5;color:#374151;">
                  ¡Bienvenido! Para activar tu cuenta y comenzar a explorar vacantes,
                  haz clic en el siguiente botón.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="border-radius:8px;background:{_PRIMARY};">
                      <a href="{verification_url}"
                         style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:bold;">
                        Verificar mi cuenta
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:13px;line-height:1.5;color:#6b7280;">
                  Si el botón no funciona, copia y pega este enlace en tu navegador:<br/>
                  <a href="{verification_url}" style="color:{_PRIMARY};word-break:break-all;">{verification_url}</a>
                </p>
                <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                  El enlace vence en 24 horas. Si no creaste esta cuenta, ignora este correo.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

    return RenderedEmail(subject=subject, html_body=html_body, text_body=text_body)


def render_interview_invitation_email(
    candidate_first_name: str,
    vacancy_name: str,
    scheduled_at: datetime,
    join_url: str,
) -> RenderedEmail:
    """Interview invitation for a candidate (Spanish, Ecuador), carrying the
    Teams join link and the date/time shown in Ecuador local time."""
    local = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=UTC)
    when = local.astimezone(_EC_TZ).strftime("%d/%m/%Y %H:%M")
    greeting = f"Hola {candidate_first_name}," if candidate_first_name else "Hola,"

    subject = f"Invitación a entrevista — {vacancy_name}"
    text_body = (
        f"{greeting}\n\n"
        f"Te invitamos a una entrevista para la vacante \"{vacancy_name}\".\n\n"
        f"Fecha y hora: {when} (hora de Ecuador)\n"
        f"Enlace de la reunión (Microsoft Teams): {join_url}\n\n"
        "Te esperamos. Gracias por tu interés en Integrity Solutions."
    )

    html_body = f"""\
<!DOCTYPE html>
<html lang="es">
  <body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="520" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
            <tr>
              <td style="background:{_PRIMARY};padding:24px 32px;color:#ffffff;font-size:18px;font-weight:bold;">
                Integrity Solutions
              </td>
            </tr>
            <tr>
              <td style="padding:32px;color:#111827;">
                <h1 style="margin:0 0 12px;font-size:22px;">Invitación a entrevista</h1>
                <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:#374151;">{greeting}</p>
                <p style="margin:0 0 20px;font-size:15px;line-height:1.5;color:#374151;">
                  Te invitamos a una entrevista para la vacante <strong>{vacancy_name}</strong>.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 24px;">
                  <tr>
                    <td style="border-radius:8px;background:#eff6ff;border:1px solid #bfdbfe;padding:16px 20px;color:#111827;font-size:15px;">
                      <strong>Fecha y hora:</strong> {when} (hora de Ecuador)
                    </td>
                  </tr>
                </table>
                <table role="presentation" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="border-radius:8px;background:{_PRIMARY};">
                      <a href="{join_url}" style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:bold;">
                        Unirme a la reunión (Teams)
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:13px;line-height:1.5;color:#6b7280;">
                  Si el botón no funciona, copia y pega este enlace:<br/>
                  <a href="{join_url}" style="color:{_PRIMARY};word-break:break-all;">{join_url}</a>
                </p>
                <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                  Te esperamos. Gracias por tu interés en Integrity Solutions.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

    return RenderedEmail(subject=subject, html_body=html_body, text_body=text_body)


def render_stage_change_email(
    candidate_first_name: str, vacancy_name: str, stage_name: str
) -> RenderedEmail:
    """Stage-change notification for a candidate (Spanish, Ecuador).

    Neutral wording ("ahora se encuentra en la etapa") so it reads correctly for
    both forward moves and corrections, regardless of the stage's meaning.
    """
    subject = f"Actualización de tu postulación — {vacancy_name}"
    greeting = f"Hola {candidate_first_name}," if candidate_first_name else "Hola,"

    text_body = (
        f"{greeting}\n\n"
        f"Tu postulación para la vacante \"{vacancy_name}\" ahora se encuentra en "
        f"la etapa: {stage_name}.\n\n"
        "Te contactaremos con los próximos pasos. Gracias por tu interés en "
        "Integrity Solutions."
    )

    html_body = f"""\
<!DOCTYPE html>
<html lang="es">
  <body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="520" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
            <tr>
              <td style="background:{_PRIMARY};padding:24px 32px;color:#ffffff;font-size:18px;font-weight:bold;">
                Integrity Solutions
              </td>
            </tr>
            <tr>
              <td style="padding:32px;color:#111827;">
                <h1 style="margin:0 0 12px;font-size:22px;">Actualización de tu postulación</h1>
                <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:#374151;">
                  {greeting}
                </p>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.5;color:#374151;">
                  Tu postulación para la vacante <strong>{vacancy_name}</strong> ahora se
                  encuentra en la etapa:
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                  <tr>
                    <td style="border-radius:8px;background:#eff6ff;border:1px solid #bfdbfe;padding:16px 20px;color:{_PRIMARY};font-size:16px;font-weight:bold;text-align:center;">
                      {stage_name}
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:14px;line-height:1.5;color:#6b7280;">
                  Te contactaremos con los próximos pasos. Gracias por tu interés en Integrity Solutions.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

    return RenderedEmail(subject=subject, html_body=html_body, text_body=text_body)


def render_interview_slot_offer_email(
    candidate_first_name: str,
    vacancy_name: str,
    offered_slots: list[dict],
    choose_url: str,
) -> RenderedEmail:
    """Slot-selection email for Mode B (Spanish, Ecuador).

    Sent to the candidate with a link they click to choose from the offered
    interview time slots. `choose_url` points to the frontend self-scheduling page.
    `offered_slots` is a list of {start, end} dicts with UTC ISO-8601 strings.
    """
    greeting = f"Hola {candidate_first_name}," if candidate_first_name else "Hola,"
    subject = f"Selecciona tu horario de entrevista — {vacancy_name}"

    # Build human-readable slot list in Ecuador local time
    def _fmt(iso_str: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            local = dt.astimezone(_EC_TZ)
            return local.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return iso_str

    slots_text_lines = []
    slots_html_rows = ""
    for i, slot in enumerate(offered_slots, 1):
        start_str = _fmt(str(slot.get("start", "")))
        end_str = _fmt(str(slot.get("end", "")))
        slots_text_lines.append(f"  {i}. {start_str} – {end_str} (hora de Ecuador)")
        slots_html_rows += (
            f'<tr><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;'
            f'font-size:14px;color:#374151;">'
            f"{i}. {start_str} – {end_str} (hora de Ecuador)"
            f"</td></tr>"
        )

    slots_text = "\n".join(slots_text_lines)

    text_body = (
        f"{greeting}\n\n"
        f'Te hemos seleccionado para una entrevista de la vacante "{vacancy_name}".\n\n'
        "Por favor, elige uno de los siguientes horarios disponibles:\n\n"
        f"{slots_text}\n\n"
        f"Para confirmar tu horario, visita este enlace:\n{choose_url}\n\n"
        "El enlace estará disponible por 7 días. Gracias por tu interés en Integrity Solutions."
    )

    html_body = f"""\
<!DOCTYPE html>
<html lang="es">
  <body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="520" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
            <tr>
              <td style="background:{_PRIMARY};padding:24px 32px;color:#ffffff;font-size:18px;font-weight:bold;">
                Integrity Solutions
              </td>
            </tr>
            <tr>
              <td style="padding:32px;color:#111827;">
                <h1 style="margin:0 0 12px;font-size:22px;">Selecciona tu horario de entrevista</h1>
                <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:#374151;">{greeting}</p>
                <p style="margin:0 0 20px;font-size:15px;line-height:1.5;color:#374151;">
                  Te hemos seleccionado para una entrevista de la vacante
                  <strong>{vacancy_name}</strong>. Por favor, elige uno de los siguientes horarios:
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
                       style="margin:0 0 24px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
                  {slots_html_rows}
                </table>
                <table role="presentation" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="border-radius:8px;background:{_PRIMARY};">
                      <a href="{choose_url}"
                         style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:bold;">
                        Elegir mi horario
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:13px;line-height:1.5;color:#6b7280;">
                  Si el botón no funciona, copia y pega este enlace en tu navegador:<br/>
                  <a href="{choose_url}" style="color:{_PRIMARY};word-break:break-all;">{choose_url}</a>
                </p>
                <p style="margin:16px 0 0;font-size:13px;color:#6b7280;">
                  El enlace estará disponible por 7 días. Gracias por tu interés en Integrity Solutions.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

    return RenderedEmail(subject=subject, html_body=html_body, text_body=text_body)
