from sqlalchemy import create_engine, pool
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.automap import automap_base
from contextlib import contextmanager

from guardian.shortcuts import assign_perm
from django.contrib.auth.models import Group
from django.conf import settings

def powers_of_two(num):
    powers = []
    i = 1
    while i <= num:
        if i & num:
            powers.append(i)
        i <<= 1
    return powers

@contextmanager
def _get_session(db_address):
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.metadata.bind = engine

    db_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = db_session()

    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()

def update_permissions(groupid, permission, obj, snex1_groups):
    """
    Updates permissions of a specific group for a certain target
    or reduceddatum

    Parameters
    ----------
    groupid: int, corresponding to which groups in SNex1 have permissions for this object
    permissionid: int, the permission name in the SNex2 db for this permission
    obj: Django model instance, e.g. Target or ReducedDatum
    snex1_groups: list of groups from snex1 to assign permissions with
    """

    target_groups = powers_of_two(groupid)

    for g_name, g_id in snex1_groups.items():
        if g_id in target_groups:
            snex2_group = Group.objects.filter(name=g_name).first()
            assign_perm(permission, snex2_group, obj)

def _return_session(db_address=settings.SNEX1_DB_URL):
    ### This one is not run within a with loop, must be closed manually
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.metadata.bind = engine

    db_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = db_session()

    return session

def _load_table(tablename, db_address):
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.prepare(engine, reflect=True)

    table = getattr(Base.classes, tablename)
    return(table)