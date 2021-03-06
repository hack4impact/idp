from flask import current_app
from flask_login import AnonymousUserMixin, UserMixin
from itsdangerous import BadSignature, SignatureExpired
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db, login_manager
from . import Application, DefaultChecklistItem, SurveyQuestion, SurveyResponse, UserChecklistItem

def db_add_commit(item):
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()

class Permission:
    GENERAL = 0x01
    SCREENER = 0x02
    ADVISOR = 0x03
    ADMIN = 0x04


class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    index = db.Column(db.String(64))
    default = db.Column(db.Boolean, default=False, index=True)
    permissions = db.Column(db.Integer)
    users = db.relationship('User', backref='role', lazy='dynamic')

    @staticmethod
    def insert_roles():
        roles = {
            'User': (Permission.GENERAL, 'main', True),
            'Screener': (Permission.SCREENER, 'screener', False),
            'Advisor': (Permission.ADVISOR, 'advisor', False),
            'Administrator': (
                Permission.ADMIN,
                'admin',
                True  # grants all permissions
            ),
        }
        for r in roles:
            role = Role.query.filter_by(name=r).first()
            if role is None:
                role = Role(name=r)
            role.permissions = roles[r][0]
            role.index = roles[r][1]
            role.default = roles[r][2]
            db.session.add(role)
        db.session.commit()

    def __repr__(self):
        return '<Role \'%s\'>' % self.name


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    confirmed = db.Column(db.Boolean, default=False)
    first_name = db.Column(db.String(64), index=True)
    last_name = db.Column(db.String(64), index=True)
    email = db.Column(db.String(64), unique=True, index=True)
    phone_number = db.Column(db.String(32), index=True)
    password_hash = db.Column(db.String(128))
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))
    location = db.Column(db.String(64))
    clemency_familiarity = db.Column(db.Text())
    law_experience = db.Column(db.Text())
    immigrant_experience = db.Column(db.Text())
    crime_experience = db.Column(db.Text())
    bio = db.Column(db.Text())
    languages = db.Column(db.Text())

    application_id = db.Column(db.Integer, db.ForeignKey('application.id', use_alter=True))
    application = db.relationship("Application", foreign_keys=[application_id], uselist=False, backref='user')

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)
        if self.role is None:
            if self.email == current_app.config['ADMIN_EMAIL']:
                self.role = Role.query.filter_by(index='admin').first()
            elif self.email == current_app.config['SCREENER_EMAIL']:
                self.role = Role.query.filter_by(index='screener').first()
            elif self.email == current_app.config['ADVISOR_EMAIL']:
                self.role = Role.query.filter_by(index='advisor').first()
            else:
                self.role = Role.query.filter_by(index='main').first()

    def full_name(self):
        return '%s %s' % (self.first_name, self.last_name)

    def can(self, permissions):
        return self.role is not None and \
            (self.role.permissions & permissions) == permissions

    def is_applicant(self):
        return self.role_id == 1

    def is_screener(self):
        return self.role_id == 2

    def is_advisor(self):
        return self.role_id == 3

    def is_admin(self):
        return self.role_id == 4

    @property
    def password(self):
        raise AttributeError('`password` is not a readable attribute')

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    def generate_confirmation_token(self, expiration=604800):
        """Generate a confirmation token to email a new user."""

        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'confirm': self.id})

    def generate_email_change_token(self, new_email, expiration=3600):
        """Generate an email change token to email an existing user."""
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'change_email': self.id, 'new_email': new_email})

    def generate_password_reset_token(self, expiration=3600):
        """
        Generate a password reset change token to email to an existing user.
        """
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'reset': self.id})

    def confirm_account(self, token):
        """Verify that the provided token is for this user's id."""
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except (BadSignature, SignatureExpired):
            return False
        if data.get('confirm') != self.id:
            return False
        self.confirmed = True
        db.session.add(self)
        db.session.commit()
        return True

    def change_email(self, token):
        """Verify the new email for this user."""
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except (BadSignature, SignatureExpired):
            return False
        if data.get('change_email') != self.id:
            return False
        new_email = data.get('new_email')
        if new_email is None:
            return False
        if self.query.filter_by(email=new_email).first() is not None:
            return False
        self.email = new_email
        db_add_commit(self)
        return True

    def reset_password(self, token, new_password):
        """Verify the new password for this user."""
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except (BadSignature, SignatureExpired):
            return False
        if data.get('reset') != self.id:
            return False
        self.password = new_password
        db_add_commit(self)
        return True

    @staticmethod
    def generate_fake(count=100, **kwargs):
        """Generate a number of fake users for testing."""
        from random import seed, choice
        from faker import Faker

        fake = Faker()
        Role.insert_roles()
        roles = Role.query.all()
        questions = SurveyQuestion.query.all()
        default_checklist_items = DefaultChecklistItem.query.all()

        def add_application_details(application):
            # Create responses to survey questions
            for question in questions:
                db.session.add(SurveyResponse(
                    content=fake.sentence(),
                    question_content=question.content,
                    application_id=application.id,
                ))
            # Create user checklist items
            for default_checklist_item in default_checklist_items:
                db.session.add(UserChecklistItem(
                    title=default_checklist_item.title,
                    description=default_checklist_item.description,
                    completed=False,
                    application_id=application.id,
                ))
            db.session.commit()

        seed()
        for role in roles:
            for i in range(count):
                user = User(
                    first_name=fake.first_name(),
                    last_name=fake.last_name(),
                    email=fake.email(),
                    phone_number=fake.phone_number(),
                    password='password',
                    confirmed=True,
                    role=role,
                    location=fake.city(),
                    clemency_familiarity=fake.text(),
                    law_experience=fake.text(),
                    immigrant_experience=fake.text(),
                    crime_experience=fake.text(),
                    bio=fake.text(),
                    languages=fake.sentence(),
                    **kwargs)
                if user.role.permissions == Permission.GENERAL:
                    # Create application
                    application = Application()
                    user.application = application
                    db_add_commit(application)
                    add_application_details(application)
                db_add_commit(user)
        user = User.query.filter_by(email='user@idp.com').first()
        if user:
            add_application_details(user.application)

    def __repr__(self):
        return '<User \'%s\'>' % self.full_name()


class AnonymousUser(AnonymousUserMixin):
    def can(self, _):
        return False

    def is_admin(self):
        return False


login_manager.anonymous_user = AnonymousUser


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
