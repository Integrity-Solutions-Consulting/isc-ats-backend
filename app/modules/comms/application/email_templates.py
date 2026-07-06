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


def render_reactivation_email(reactivation_url: str) -> RenderedEmail:
    """Sent when a previously-closed candidate account registers again.

    `reactivation_url` is the same verification link used for first-time
    activation; clicking it switches the account back on and restores the
    candidate profile. Wording makes the reactivation explicit (honest UX).
    """
    subject = "Reactiva tu cuenta — Integrity Solutions"

    text_body = (
        "¡Qué bueno tenerte de vuelta!\n\n"
        "Recibimos una solicitud para reactivar tu cuenta. Para volver a "
        "ingresar y recuperar tu perfil, abre este enlace:\n"
        f"{reactivation_url}\n\n"
        "El enlace vence en 24 horas. Si no solicitaste esto, ignora este correo."
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
                <h1 style="margin:0 0 12px;font-size:22px;">Reactiva tu cuenta</h1>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.5;color:#374151;">
                  ¡Qué bueno tenerte de vuelta! Para reactivar tu cuenta y
                  recuperar tu perfil, haz clic en el siguiente botón.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="border-radius:8px;background:{_PRIMARY};">
                      <a href="{reactivation_url}"
                         style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:bold;">
                        Reactivar mi cuenta
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:13px;line-height:1.5;color:#6b7280;">
                  Si el botón no funciona, copia y pega este enlace en tu navegador:<br/>
                  <a href="{reactivation_url}" style="color:{_PRIMARY};word-break:break-all;">{reactivation_url}</a>
                </p>
                <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                  El enlace vence en 24 horas. Si no solicitaste esto, ignora este correo.
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


def render_account_exists_email(login_url: str) -> RenderedEmail:
    """Sent when someone tries to register with an already-registered email.

    The registration API answers generically (it never reveals whether an email
    exists) to prevent account enumeration; the real owner is informed here, on a
    channel only they control. Wording is reassuring and points to login.
    """
    subject = "Ya tienes una cuenta — Integrity Solutions"

    text_body = (
        "Hola,\n\n"
        "Recibimos un intento de registro con este correo, pero ya tienes una "
        "cuenta con nosotros.\n\n"
        f"Para ingresar, abre este enlace:\n{login_url}\n\n"
        "Si no intentaste registrarte, puedes ignorar este mensaje con tranquilidad."
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
                <h1 style="margin:0 0 12px;font-size:22px;">Ya tienes una cuenta</h1>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.5;color:#374151;">
                  Recibimos un intento de registro con este correo, pero ya tienes
                  una cuenta con nosotros. Puedes ingresar con el siguiente botón.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="border-radius:8px;background:{_PRIMARY};">
                      <a href="{login_url}"
                         style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:bold;">
                        Iniciar sesión
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                  Si no intentaste registrarte, puedes ignorar este mensaje con tranquilidad.
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


def render_password_reset_email(reset_url: str) -> RenderedEmail:
    """Password-reset email (Spanish, Ecuador).

    `reset_url` is the full link to the frontend page where the user sets a new
    password; it embeds the single-use reset token as a query parameter.
    """
    subject = "Restablece tu contraseña — Integrity Solutions"

    text_body = (
        "Recibimos una solicitud para restablecer la contraseña de tu cuenta.\n\n"
        "Para elegir una nueva contraseña, abre este enlace:\n"
        f"{reset_url}\n\n"
        "El enlace vence en 1 hora y solo puede usarse una vez. Si no solicitaste "
        "este cambio, ignora este correo: tu contraseña actual seguirá funcionando."
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
                <h1 style="margin:0 0 12px;font-size:22px;">Restablece tu contraseña</h1>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.5;color:#374151;">
                  Recibimos una solicitud para restablecer la contraseña de tu cuenta.
                  Haz clic en el siguiente botón para elegir una nueva.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="border-radius:8px;background:{_PRIMARY};">
                      <a href="{reset_url}"
                         style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:bold;">
                        Restablecer contraseña
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:13px;line-height:1.5;color:#6b7280;">
                  Si el botón no funciona, copia y pega este enlace en tu navegador:<br/>
                  <a href="{reset_url}" style="color:{_PRIMARY};word-break:break-all;">{reset_url}</a>
                </p>
                <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                  El enlace vence en 1 hora y solo puede usarse una vez. Si no solicitaste
                  este cambio, ignora este correo: tu contraseña actual seguirá funcionando.
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


def render_rejection_email(
    candidate_first_name: str, vacancy_name: str
) -> RenderedEmail:
    """Rejection notification for a candidate (Spanish, Ecuador).

    Professional, empathetic wording explaining the candidate does not meet the
    profile requirements for this specific vacancy, encouraging them to apply to
    future openings.  Does NOT say "the process has concluded" because the
    vacancy may still be published and that phrasing would feel dishonest.
    """
    subject = f"Actualización de tu postulación — {vacancy_name}"
    greeting = f"Hola {candidate_first_name}," if candidate_first_name else "Hola,"

    text_body = (
        f"{greeting}\n\n"
        f"Agradecemos tu interés en la vacante \"{vacancy_name}\" y el tiempo que "
        "dedicaste al proceso de selección.\n\n"
        "Después de revisar cuidadosamente los perfiles, en esta ocasión hemos "
        "decidido avanzar con candidatos cuyo perfil se ajusta más a los "
        "requerimientos específicos de esta posición.\n\n"
        "Te animamos a que continúes revisando nuestras vacantes disponibles; "
        "tu perfil podría encajar perfectamente en una futura oportunidad.\n\n"
        "Te deseamos mucho éxito en tu búsqueda profesional.\n\n"
        "Atentamente,\nIntegrity Solutions"
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
                <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:#374151;">
                  Agradecemos tu interés en la vacante <strong>{vacancy_name}</strong>
                  y el tiempo que dedicaste al proceso de selección.
                </p>
                <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:#374151;">
                  Después de revisar cuidadosamente los perfiles, en esta ocasión hemos
                  decidido avanzar con candidatos cuyo perfil se ajusta más a los
                  requerimientos específicos de esta posición.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                  <tr>
                    <td style="border-radius:8px;background:#eff6ff;border:1px solid #bfdbfe;padding:16px 20px;color:#374151;font-size:14px;line-height:1.5;">
                      Te animamos a que continúes revisando nuestras vacantes disponibles;
                      tu perfil podría encajar perfectamente en una futura oportunidad.
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:14px;line-height:1.5;color:#6b7280;">
                  Te deseamos mucho éxito en tu búsqueda profesional.
                </p>
                <p style="margin:16px 0 0;font-size:14px;color:#6b7280;">
                  Atentamente,<br/>Integrity Solutions
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


def render_slot_confirmed_email(
    interviewer_first_name: str,
    candidate_full_name: str,
    vacancy_name: str,
    scheduled_at: datetime,
    join_url: str | None,
) -> RenderedEmail:
    """Slot-confirmed notification for the interviewer / HR (Spanish, Ecuador).

    Sent after a candidate picks a time slot (Mode B) and the Teams meeting has
    been created.  Includes the join link when available.
    """
    local = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=UTC)
    when = local.astimezone(_EC_TZ).strftime("%d/%m/%Y %H:%M")
    greeting = (
        f"Hola {interviewer_first_name},"
        if interviewer_first_name
        else "Hola,"
    )

    subject = f"Entrevista confirmada — {candidate_full_name} · {vacancy_name}"
    text_body = (
        f"{greeting}\n\n"
        f"El candidato {candidate_full_name} ha confirmado su horario de entrevista "
        f"para la vacante \"{vacancy_name}\".\n\n"
        f"Fecha y hora: {when} (hora de Ecuador)\n"
    )
    if join_url:
        text_body += f"Enlace de la reunión (Microsoft Teams): {join_url}\n"
    text_body += "\nIntegrity Solutions"

    join_button = ""
    if join_url:
        join_button = f"""\
                <table role="presentation" cellpadding="0" cellspacing="0" style="margin:20px 0 0;">
                  <tr>
                    <td style="border-radius:8px;background:{_PRIMARY};">
                      <a href="{join_url}" style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:bold;">
                        Unirme a la reunión (Teams)
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:16px 0 0;font-size:13px;line-height:1.5;color:#6b7280;">
                  Si el botón no funciona, copia y pega este enlace:<br/>
                  <a href="{join_url}" style="color:{_PRIMARY};word-break:break-all;">{join_url}</a>
                </p>"""

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
                <h1 style="margin:0 0 12px;font-size:22px;">Entrevista confirmada</h1>
                <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:#374151;">{greeting}</p>
                <p style="margin:0 0 20px;font-size:15px;line-height:1.5;color:#374151;">
                  El candidato <strong>{candidate_full_name}</strong> ha confirmado su horario
                  de entrevista para la vacante <strong>{vacancy_name}</strong>.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 4px;">
                  <tr>
                    <td style="border-radius:8px;background:#eff6ff;border:1px solid #bfdbfe;padding:16px 20px;color:#111827;font-size:15px;">
                      <strong>Candidato:</strong> {candidate_full_name}<br/>
                      <strong>Fecha y hora:</strong> {when} (hora de Ecuador)
                    </td>
                  </tr>
                </table>{join_button}
                <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                  Integrity Solutions
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


def render_random_password_email(email: str, password_raw: str) -> RenderedEmail:
    """Random password email for a new staff user."""
    from app.core.config import settings
    subject = "Tu cuenta ha sido creada — Integrity Solutions"
    login_url = f"{settings.frontend_base_url}/login"

    text_body = (
        "¡Hola!\n\n"
        "Tu cuenta de usuario en el portal de reclutamiento de Integrity Solutions ha sido creada.\n\n"
        f"Correo electrónico: {email}\n"
        f"Contraseña temporal: {password_raw}\n\n"
        "Por motivos de seguridad, debes ingresar al sistema y cambiar tu contraseña de inmediato:\n"
        f"{login_url}\n\n"
        "Si no reconoces este correo o no solicitaste la creación de esta cuenta, por favor contacta con soporte."
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
                <h1 style="margin:0 0 12px;font-size:22px;">Tu cuenta ha sido creada</h1>
                <p style="margin:0 0 20px;font-size:15px;line-height:1.5;color:#374151;">
                  Tu cuenta de usuario en el portal de reclutamiento de Integrity Solutions ha sido creada exitosamente.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:0 0 24px;border:1px solid #e5e7eb;border-radius:8px;background:#f9fafb;padding:16px 20px;color:#111827;font-size:15px;line-height:1.6;">
                  <tr>
                    <td>
                      <strong>Usuario:</strong> {email}<br/>
                      <strong>Contraseña temporal:</strong> <code style="font-family:monospace;background:#e5e7eb;padding:2px 6px;border-radius:4px;font-size:14px;">{password_raw}</code>
                    </td>
                  </tr>
                </table>
                <p style="margin:0 0 24px;font-size:15px;line-height:1.5;color:#374151;">
                  Por motivos de seguridad, debes ingresar al sistema y cambiar tu contraseña de inmediato.
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="border-radius:8px;background:{_PRIMARY};">
                      <a href="{login_url}"
                         style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:bold;">
                        Iniciar sesión y cambiar contraseña
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                  Si no reconoces este correo o no solicitaste la creación de esta cuenta, por favor contacta con soporte.
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
