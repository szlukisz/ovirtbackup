import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

def send_mail(sender = None,
             to = None,
             subject = None,
             body = None,
             port = None,
             server = None,
             password = None,
             attachmentFile = None,
             replaceWith = None):

    message = MIMEMultipart()
    message['From'] = sender
    message['To'] = to
    with open(body, 'r') as myfile:
        mail_content = myfile.read()

    if replaceWith:
        for rep in replaceWith:
            mail_content = mail_content.replace(rep[0], rep[1])
            subject = subject.replace(rep[0], rep[1])

    message['Subject'] = subject


    message.attach(MIMEText(mail_content, 'plain'))
    if attachmentFile:
        with open(attachmentFile, "rb") as attachment:
        # Add file as application/octet-stream
        # Email client can usually download this automatically as attachment
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())

        # Encode file in ASCII characters to send by email
        encoders.encode_base64(part)

        # Add header as key/value pair to attachment part
        filename = os.path.basename(attachmentFile)
        # part.add_header(
        #     "Content-Disposition",
        #     f"attachment; filename={filename}",
        #     )
        part.add_header('Content-Disposition', 'attachment', filename=filename)

        message.attach(part)

    text = message.as_string()
    s = smtplib.SMTP(server, port)
    s.starttls()
    s.login(sender, password)
    s.sendmail(sender, to, text)
    s.quit()
