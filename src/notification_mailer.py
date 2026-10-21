from notification_core import *

def build_message(sender_name: str, sender: str, recipients: list[str], subject: str, text: str, html_body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, sender))
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
    message["Auto-Submitted"] = "auto-generated"  # suppresses out-of-office auto-replies
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    return message


def send_email(message: EmailMessage, sender: str, recipients: list[str], host: str, port: int, login: str, key: str) -> None:
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        try:
            deliver(message, sender, recipients, host, port, login, key)
            return
        except TransientError as exc:
            if attempt == attempts:
                raise SendError(f"temporary SMTP failure persisted after {attempts} attempts: {exc}") from None
            delay = RETRY_DELAYS[attempt - 1]
            log("warning", f"SMTP attempt {attempt}/{attempts} failed ({exc}); retrying in {delay}s.")
            time.sleep(delay)


def smtp_reason(exc: smtplib.SMTPResponseException) -> str:
    detail = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
    return clean(f"{exc.smtp_code} {detail}", 200)


def deliver(message: EmailMessage, sender: str, recipients: list[str], host: str, port: int, login: str, key: str) -> None:
    """One SMTP session. Errors are mapped to TransientError (retry) or SendError (stop)."""
    try:
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
    except smtplib.SMTPResponseException as exc:
        raise TransientError(f"connection refused by server: {smtp_reason(exc)}") from None
    except OSError as exc:
        raise TransientError(f"cannot connect to {host}:{port} ({type(exc).__name__})") from None

    try:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(login, key)
        refused = server.send_message(message, from_addr=sender, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as exc:
        raise SendError(
            f"authentication failed ({smtp_reason(exc)}). Check BREVO_SMTP_LOGIN (the SMTP login, not the "
            "account e-mail) and BREVO_SMTP_KEY (an SMTP key, not an API key)."
        ) from None
    except smtplib.SMTPNotSupportedError:
        raise SendError("the server does not support STARTTLS/AUTH on this port.") from None
    except smtplib.SMTPSenderRefused as exc:
        if 400 <= exc.smtp_code < 500:
            raise TransientError(f"sender temporarily refused: {smtp_reason(exc)}") from None
        raise SendError(f"sender refused ({smtp_reason(exc)}). Is NOTIFICATION_FROM verified in Brevo?") from None
    except smtplib.SMTPRecipientsRefused:
        raise SendError(f"all {len(recipients)} recipient(s) were refused by the server.") from None
    except smtplib.SMTPResponseException as exc:
        if 400 <= exc.smtp_code < 500:
            raise TransientError(smtp_reason(exc)) from None
        raise SendError(f"server rejected the message ({smtp_reason(exc)}). Daily quota reached?") from None
    except smtplib.SMTPServerDisconnected:
        raise TransientError("server closed the connection") from None
    except ssl.SSLCertVerificationError:
        raise SendError("TLS certificate verification failed.") from None
    except smtplib.SMTPException as exc:
        raise SendError(f"SMTP error ({type(exc).__name__}).") from None
    except OSError as exc:
        raise TransientError(f"network error ({type(exc).__name__})") from None
    finally:
        try:
            server.quit()
        except Exception:  # the message is already accepted (or failed); never retry because of QUIT
            server.close()

    if refused:
        log("warning", f"{len(refused)} of {len(recipients)} recipient(s) were refused by the server.")


