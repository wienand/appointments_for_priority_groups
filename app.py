import collections
import datetime
import locale
import logging
import logging.handlers
import secrets
import smtplib
import sys
from email.mime.text import MIMEText

import flask_sqlalchemy
import flask_wtf
import sqlalchemy as sa
import validate_email
import waitress
import wtforms
from flask import Flask, render_template, redirect, session, request
from werkzeug.exceptions import Forbidden

from flask_session.__init__ import Session

locale.setlocale(locale.LC_ALL, "german")

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s', filename='server.log')
streamHandler = logging.StreamHandler()
streamHandler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(streamHandler)

app = Flask(__name__, static_folder='static', static_url_path='')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SESSION_PERMANENT'] = False

app.config.from_envvar('TFP_SETTINGS')
app.config.from_object(__name__)
Session(app)
db = flask_sqlalchemy.SQLAlchemy(app)

days = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
begin_times_as_time = [datetime.time(hour=i) for i in range(7, 21)]
begin_times = ['ab %s Uhr' % i for i in range(7, 21)]


class Entitlement(db.Model):
    __tablename__ = 'entitlement'
    id = sa.Column(sa.types.Integer, primary_key=True)
    created_at = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)

    token = sa.Column(sa.types.UnicodeText, nullable=False, unique=True)
    name = sa.Column(sa.types.UnicodeText, default='')


class Candidate(db.Model):
    __tablename__ = 'candidate'
    id = sa.Column(sa.types.Integer, primary_key=True)
    created_at = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)
    modified_at = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)
    entitlement_lookup_counter = sa.Column(sa.types.Integer, default=0)
    last_token_provision = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)
    last_login = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)

    entitlement_id = sa.Column(sa.types.Integer, sa.ForeignKey('entitlement.id'))
    reason = sa.Column(sa.types.UnicodeText, default='')
    last_name = sa.Column(sa.types.UnicodeText, default='')
    first_name = sa.Column(sa.types.UnicodeText, default='')
    email = sa.Column(sa.types.UnicodeText, nullable=False, unique=True)
    token = sa.Column(sa.types.UnicodeText, nullable=False, unique=True, default=lambda: secrets.token_hex(16))

    Entitlement = db.relationship('Entitlement', backref=db.backref('Candidates'))


class Availability(db.Model):
    __tablename__ = 'availability'
    id = sa.Column(sa.types.Integer, primary_key=True)
    created_at = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)
    modified_at = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)

    candidate_id = sa.Column(sa.types.Integer, sa.ForeignKey('candidate.id'))
    weekday = sa.Column(sa.types.UnicodeText, nullable=False)
    begin = sa.Column(sa.types.Time, nullable=False)
    available = sa.Column(sa.types.UnicodeText, nullable=False)

    Candidate = db.relationship('Candidate', backref=db.backref('Availabilities', lazy=False))


class Appointment(db.Model):
    __tablename__ = 'appointment'
    id = sa.Column(sa.types.Integer, primary_key=True)
    candidate_id = sa.Column(sa.types.Integer, sa.ForeignKey('candidate.id'))
    created_at = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)
    modified_at = sa.Column(sa.types.DateTime, default=datetime.datetime.utcnow)
    slot = sa.Column(sa.types.DateTime, nullable=False)
    confirmed = sa.Column(sa.types.Boolean, default=False)
    rejected = sa.Column(sa.types.Boolean, default=False)
    token = sa.Column(sa.types.UnicodeText, nullable=False, unique=True, default=lambda: secrets.token_hex(16))

    Candidate = db.relationship('Candidate', backref=db.backref('Appointments', lazy=False))


class EntitlementForm(flask_wtf.FlaskForm):
    entitlement = wtforms.StringField('permission_token', validators=[wtforms.validators.InputRequired(), wtforms.validators.Length(12, 36)])


class LoginForm(EntitlementForm):
    # entitlement = wtforms.StringField('permission_token', validators=[wtforms.validators.InputRequired(), wtforms.validators.Length(12, 36)])
    email = wtforms.StringField('email', validators=[wtforms.validators.InputRequired(), wtforms.validators.Length(6, 254)])


class AvailabilityForm(flask_wtf.FlaskForm):
    first_name = wtforms.StringField('first_name', validators=[wtforms.validators.InputRequired(), wtforms.validators.Length(2, 254)])
    last_name = wtforms.StringField('last_name', validators=[wtforms.validators.InputRequired(), wtforms.validators.Length(2, 254)])
    reason = wtforms.StringField('reason', validators=[wtforms.validators.InputRequired(), wtforms.validators.Length(2, 254)])


def send_login_link(candidate):
    template = app.config['SMTP_LOGIN_TEMPLATE']
    message = template['body'].format(base_url=app.config['BASE_URL'], token=candidate.token)
    subject = template['subject'].format(base_url=app.config['BASE_URL'], token=candidate.token)
    with smtplib.SMTP(app.config['SMTP_HOST']) as smtp:
        msg = MIMEText(message)
        msg['Subject'] = subject
        msg['From'] = app.config['SMTP_FROM']
        msg['To'] = candidate.email
        body = msg.as_string()
        logging.info('Send message to %s (from: %s): %s', [candidate.email] + app.config.get('SMTP_BCC', []), app.config['SMTP_FROM'], body.replace('\n', '\\n'))
        smtp.sendmail(app.config['SMTP_FROM'], [candidate.email] + app.config.get('SMTP_BCC', []), body)


def get_availability_matrix(candidate):
    availability_matrix = collections.defaultdict(lambda: collections.defaultdict(lambda: False))
    for availability in candidate.Availabilities:
        availability_matrix[begin_times_as_time.index(availability.begin)][days.index(availability.weekday)] = availability.available == 'NORMAL'
    return availability_matrix


@app.route('/')
def hello_world():
    return redirect('/personal')


@app.route('/personal', methods=['GET', 'POST'])
def route_index():
    form = LoginForm(request.form or request.args)
    logging.debug('Register with: %s/%s', form.entitlement.data, form.email.data)
    if form.validate_on_submit():
        if not validate_email.validate_email(form.email.data):
            return render_template('index.html', form=form, no_email=True, no_entitlement=False)
        candidate = Candidate.query.filter_by(email=form.email.data.strip().lower()).one_or_none()
        if not candidate:
            entitlement = Entitlement.query.filter_by(token=form.entitlement.data.strip().lower()).one_or_none()
            candidate = Candidate(email=form.email.data.strip().lower(), Entitlement=entitlement)
            db.session.add(candidate)
            if entitlement:
                logging.info('Creating candidate %s with entitlement <%s: %s>', candidate.email, candidate.Entitlement.name, candidate.Entitlement.id)
            else:
                logging.warning('Creating candidate %s without entitlement (tried: %s)', candidate.email, form.entitlement.data.strip().lower())
        candidate.last_token_provision = datetime.datetime.utcnow()
        db.session.commit()
        send_login_link(candidate)
        return render_template('success-sent.html', candidate=candidate)

    return render_template('index.html', form=form, no_email=False, no_entitlement=False)


@app.route('/login-availability/<token>')
def route_login_availability(token):
    candidate = Candidate.query.filter_by(token=token).one_or_none()
    if not candidate:
        logging.warning('Invalid candidate token: %s', token)
        raise Forbidden('Invalid candidate token: ' + token + '\n\nPlease try again from the beginning.')
    session['candidate'] = candidate.id
    if not candidate.Entitlement:
        logging.warning('No entitlement for: <%s: %s> by %s', candidate.id, candidate.email)
        return redirect('/add_entitlement')
    logging.debug('Availability for: <%s: %s> by %s', candidate.id, candidate.email, candidate.Entitlement.name)
    return redirect('/availability')


@app.route('/add_entitlement', methods=['GET', 'POST'])
def route_add_entitlement():
    if not session.get('candidate', None):
        logging.error('No session on route_add_entitlement, redirecting ...')
        return redirect('/personal')
    candidate = Candidate.query.get(session['candidate'])
    form = EntitlementForm()
    if form.validate_on_submit():
        if candidate.entitlement_lookup_counter > 9:
            if (datetime.datetime.utcnow() - candidate.modified_at).total_seconds() > 15 * 60:
                candidate.entitlement_lookup_counter = 0
            else:
                raise Forbidden('Mehr als 10 Versuche, bitte erneut in 15 min versuchen!')
        entitlement = Entitlement.query.filter_by(token=form.entitlement.data.strip().lower()).one_or_none()
        if not entitlement:
            logging.warning('Entitlement for <%s: %s> not found with: %s', candidate.id, candidate.name, form.entitlement.data.strip().lower())
            candidate.entitlement_lookup_counter += 1
            db.session.commit()
            return render_template('add-entitlement.html', form=form, no_entitlement=True)
        candidate.Entitlement = entitlement
        db.session.commit()
        return redirect('/availability')
    return render_template('add-entitlement.html', form=form, no_entitlement=False)


@app.route('/availability', methods=['GET', 'POST'])
def route_availability():
    if not session.get('candidate', None):
        logging.error('No session on route_availability, redirecting ...')
        return redirect('/personal')
    candidate = Candidate.query.get(session['candidate'])
    if not candidate.Entitlement:
        raise Forbidden('Candidate without entitlement not allowed here. Please start again.')

    form = AvailabilityForm()

    if form.validate_on_submit():
        logging.info('Storing availability for <%s: %s>: %s', candidate.id, candidate.email, request.form)
        candidate.first_name = form.first_name.data or candidate.first_name
        candidate.last_name = form.last_name.data or candidate.last_name
        candidate.reason = form.reason.data or candidate.reason
        availability_matrix_objects = collections.defaultdict(lambda: collections.defaultdict(lambda: Availability()))
        for availability in candidate.Availabilities:
            availability_matrix_objects[begin_times_as_time.index(availability.begin)][days.index(availability.weekday)] = availability
        for i, start in enumerate(begin_times_as_time):
            for j, day in enumerate(days):
                if request.form.get('%s-%s' % (i, j), None):
                    if j in availability_matrix_objects[i]:
                        if availability_matrix_objects[i][j].available != 'NORMAL':
                            availability_matrix_objects[i][j].available = 'NORMAL'
                            availability_matrix_objects[i][j].modified_at = datetime.datetime.utcnow()
                    else:
                        availability_matrix_objects[i][j] = Availability(weekday=day, begin=start, available='NORMAL', Candidate=candidate)
                        db.session.add(availability_matrix_objects[i][j])
                else:
                    if j in availability_matrix_objects[i]:
                        if availability_matrix_objects[i][j].available != 'NOT AVAILABLE':
                            availability_matrix_objects[i][j].available = 'NOT AVAILABLE'
                            availability_matrix_objects[i][j].modified_at = datetime.datetime.utcnow()
        db.session.commit()
        return redirect('/success')

    logging.debug('Showing availability for <%s: %s>', candidate.id, candidate.email)
    form.first_name.data = candidate.first_name
    form.last_name.data = candidate.last_name
    form.reason.data = candidate.reason
    availability_matrix = get_availability_matrix(candidate)
    return render_template('availability.html', form=form, days=days, begin_times=begin_times, availability_matrix=availability_matrix,
                           enumerate=enumerate)


@app.route('/success')
def route_success():
    if not session.get('candidate', None):
        logging.error('No session on route_success, redirecting ...')
        return redirect('/personal')
    candidate = Candidate.query.get(session.pop('candidate'))
    availability_matrix = get_availability_matrix(candidate)
    return render_template('success.html', candidate=candidate, days=days, begin_times=begin_times, availability_matrix=availability_matrix,
                           enumerate=enumerate)


@app.route('/view-appointment/<token>', methods=['GET', 'POST'])
def route_view_appointment(token):
    appointment = Appointment.query.filter_by(token=token).one()
    if request.form.get('submit') == 'confirm':
        appointment.confirmed = True
        appointment.modified_at = datetime.datetime.utcnow()
        db.session.commit()
    if request.form.get('submit') == 'reject':
        appointment.rejected = True
        appointment.modified_at = datetime.datetime.utcnow()
        db.session.commit()
    return render_template('view-appointment.html', appointment=appointment)


db.create_all()

if __name__ == '__main__':
    waitress.serve(app, host='127.0.0.1', port=5000 if len(sys.argv) < 2 else int(sys.argv[1]))
