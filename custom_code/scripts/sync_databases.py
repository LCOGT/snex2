#!/usr/bin/env python

from sqlalchemy import create_engine, and_, pool, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.sql import func

import json
from contextlib import contextmanager
import os
import datetime
from django.conf import settings
from tom_dataproducts.models import DataProduct, data_product_path, ReducedDatum
from django.contrib.auth.models import Group
from custom_code.utils import update_permissions
from custom_code.models import ReducedDatumExtra
from guardian.shortcuts import assign_perm
from tom_targets.models import Target, TargetName

import logging
logger = logging.getLogger(__name__)

_SNEX2_DB = 'postgresql://{}:{}@{}:{}/snex2'.format(
    os.environ.get('SNEX2_DB_USER'),
    os.getenv('SNEX2_DB_PASSWORD'),
    os.getenv('SNEX2_DB_HOST', 'snex2-db'),
    os.getenv('SNEX2_DB_PORT', 5432)
)

engine1 = create_engine(settings.SNEX1_DB_URL)
engine2 = create_engine(_SNEX2_DB)


@contextmanager
def get_session(db_address=settings.SNEX1_DB_URL):
    """
    Get a connection to a database

    Returns
    ----------
    session: SQLAlchemy database session
    """
    Base = automap_base()
    if db_address == settings.SNEX1_DB_URL:
        Base.metadata.bind = engine1
        db_session = sessionmaker(bind=engine1, autoflush=False, expire_on_commit=False)
    else:
        Base.metadata.bind = engine2
        db_session = sessionmaker(bind=engine2, autoflush=False, expire_on_commit=False)
    session = db_session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


def load_table(tablename, db_address=settings.SNEX1_DB_URL):
    """
    Load a table with its data from a database

    Parameters
    ----------
    tablename: str, the name of the table to load
    db_address: str, sqlalchemy address to the table being loaded

    Returns
    ----------
    table: sqlalchemy table object
    """
    Base = automap_base()
    engine = create_engine(db_address, poolclass=pool.NullPool)
    Base.prepare(autoload_with=engine)

    table = getattr(Base.classes, tablename)
    return table


### Define our SNex1 db tables as Classes
Db_Changes = load_table('db_changes', db_address=settings.SNEX1_DB_URL)
Photlco = load_table('photlco', db_address=settings.SNEX1_DB_URL)
Spec = load_table('spec', db_address=settings.SNEX1_DB_URL)
Targets = load_table('targets', db_address=settings.SNEX1_DB_URL)
Target_Names = load_table('targetnames', db_address=settings.SNEX1_DB_URL)
Classifications = load_table('classifications', db_address=settings.SNEX1_DB_URL)
Groups = load_table('groups', db_address=settings.SNEX1_DB_URL)
Targets = load_table('targets', db_address=settings.SNEX1_DB_URL)
Target_Names = load_table('targetnames', db_address=settings.SNEX1_DB_URL)

### Make a dictionary of the groups in the SNex1 db
with get_session(db_address=settings.SNEX1_DB_URL) as db_session:
    snex1_groups = {}
    for x in db_session.query(Groups):
        snex1_groups[x.name] = x.idcode


def query_db_changes(table, action, db_address=settings.SNEX1_DB_URL):
    """
    Query the db_changes table

    Parameters
    ----------
    table: str, table that was modified
    action: str, one of 'update', 'insert', or 'delete'
    db_address: str, sqlalchemy address to the database containing table
    """
    #table_dict = {'photlco': Photlco, 'spec': Spec}
    with get_session(db_address=db_address) as db_session:
        criteria = and_(Db_Changes.tablename==table, Db_Changes.action==action)
        record = db_session.query(Db_Changes).filter(criteria)
    return record


def get_current_row(table, id_, db_address=settings.SNEX1_DB_URL):
    """ 
    Get the row that was modified, as recorded in the db_changes table
    
    Parameters
    ----------
    table: Table, the table in the SNex1 db that was modified, i.e. Photlco
    id_: int, the id of the modified row
    db_address: str, sqlalchemy address to the database containing table
    """
    with get_session(db_address=db_address) as db_session:
        criteria = getattr(table, 'id') == id_
        record = db_session.query(table).filter(criteria).first()
    return record


def delete_row(table, id_, db_address=settings.SNEX1_DB_URL):
    """
    Deletes a given row in table
    
    Parameters
    ----------
    table: Table, the table to clear
    id_: int, id of row to delete
    db_address: str, sqlalchemy address to the db_changes table
    """
    with get_session(db_address=db_address) as db_session:
        criteria = getattr(table, 'id') == id_
        db_session.query(table).filter(criteria).delete()
        db_session.commit()


def update_phot(action):
    """
    Queries the ReducedDatum table in the SNex2 db with any changes made to the Photlco table in the SNex1 db

    Parameters
    ----------
    action: str, one of 'update', 'insert', or 'delete'
    db_address: str, sqlalchemy address to the SNex2 db
    """
    logger.info(f'{action} to photometry. . .')
    phot_result = query_db_changes('photlco', action, db_address=settings.SNEX1_DB_URL)
    logger.info(f'Total photometry changes {len([change.rowid for change in phot_result])}')

    i = 0
    for result in phot_result:
        i += 1
        logger.info(f"i={i}/{len([change.rowid for change in phot_result])}")
        try:
            id_ = result.rowid
            phot_row = get_current_row(Photlco, id_, db_address=settings.SNEX1_DB_URL)
            if action=='delete':
                logger.info("action = delete")
                ReducedDatum.objects.filter(data_type = 'photometry', value__snex_id = id_).delete()

                with get_session(db_address=settings.SNEX1_DB_URL) as db_session:
                    all_other_rows = db_session.query(Db_Changes).filter(and_(Db_Changes.tablename=='photlco', Db_Changes.rowid==id_))
                    for row in all_other_rows:
                        db_session.delete(row)
                    db_session.commit()
                    
            else:
                pipeline_id = phot_row.targetid
                dobs = phot_row.dateobs
                tobs = phot_row.ut
                if tobs is None:
                    tobs = '00:00:00'
                if dobs is None:
                    dobs = datetime.datetime.today().strftime('%Y-%m-%d')
                time = '{} {}'.format(dobs, tobs) 
                
                if int(phot_row.mag) != 9999:
                    if int(phot_row.filetype) == 1:
                        phot = {'magnitude': float(phot_row.mag), 'filter': phot_row.filter, 'error': float(phot_row.dmag), 'snex_id': int(id_), 'background_subtracted': False, 'telescope': phot_row.telescope, 'instrument': phot_row.instrument}
                    elif int(phot_row.filetype) == 3 and phot_row.difftype is not None:
                        if int(phot_row.difftype) == 0:
                            subtraction_algorithm = 'Hotpants'
                        elif int(phot_row.difftype) == 1:
                            subtraction_algorithm = 'PyZOGY'
                        filename = phot_row.filename
                        if 'SDSS' in filename:
                            template_source = 'SDSS'
                        else:
                            template_source = 'LCO'
                        phot = {'magnitude': float(phot_row.mag), 'filter': phot_row.filter, 'error': float(phot_row.dmag), 'snex_id': int(id_), 'background_subtracted': True, 'subtraction_algorithm': subtraction_algorithm, 'template_source': template_source, 'reduction_type': 'manual', 'telescope': phot_row.telescope, 'instrument': phot_row.instrument}
                
                    else:
                        phot = {'snex_id': int(id_)}
                else:
                    phot = {'snex_id': int(id_)}

                phot_groupid = phot_row.groupidcode

                with get_session(db_address=settings.SNEX1_DB_URL) as db_session:
                    standard_classification_row = db_session.query(Classifications).filter(Classifications.name=='Standard').first()
                    if standard_classification_row is not None:
                        standard_classification_id = standard_classification_row.id
                    else:
                        standard_classification_id = -1
                    standard_list = db_session.query(Targets).filter(Targets.classificationid == standard_classification_id)
                    standard_ids = [x.id for x in standard_list]
                if pipeline_id not in standard_ids and int(phot_row.filetype) in (1, 3):
                    target = Target.objects.get(pipeline_id=pipeline_id)
                    logger.info(f'phot dictionary: {phot}')

                    #check if there is a duplicate:
                    rds = ReducedDatum.objects.filter(target = target,data_type = 'photometry',value__snex_id = id_)
                    logger.info(f'how many reduced datums for snexid: {id_}? {len(rds)}')
                    for rd in rds:
                        logger.info(f'value for rd {rd.id}: {rd.value}')
                    rd_just_snex1 = [rd for rd in rds if len(rd.value) == 1]

                    if len(rd_just_snex1) < len(rds):
                        for rd in rd_just_snex1:
                            logger.info(f'deleting {rd.id} with value {rd.value}')
                            rd.delete()

                    elif len(rd_just_snex1) > 1:
                        logger.info(f'Found {len(rd_just_snex1)} placeholders, keeping only one')
                        for rd in rd_just_snex1[1:]:
                            logger.info(f'deleting duplicate placeholder {rd.id}')
                            rd.delete()

                    rd, created = ReducedDatum.objects.update_or_create(
                        target = target,
                        data_type = 'photometry',
                        value__snex_id = id_,
                        defaults = {
                            'value': phot,
                            'timestamp': time,
                            'source_name': '',
                            'source_location': '',
                        })


                    if phot_groupid is not None:
                        update_permissions(int(phot_groupid), 'view_reduceddatum', rd, snex1_groups)

        except Exception as e:
            logger.info(f"Failed to process photometry for db_changes row {result.id} photlco {result.rowid} with exception {e}")
            continue

        delete_row(Db_Changes, result.id, db_address=settings.SNEX1_DB_URL)

def read_spec(filename):
    """
    Read an ascii spectrum file and return a JSON dump-s of the wavelengths and fluxes

    Parameters
    ----------
    filename: str, the filepath+filename of the ascii file to read
    """
    spec_file = open(filename, 'r')
    lines = [x.split() for x in spec_file.readlines()]
    data = {"{}".format(i): {"wavelength": float(lines[i][0]), "flux": float(lines[i][1])} for i in range(len(lines)) if lines[i][1] != 'nan'}
    return(data)


def update_spec(action):
    """
    Queries the ReducedDatum table in the SNex2 db with any changes made to the Spec table in the SNex1 db

    Parameters
    ----------
    action: str, one of 'update', 'insert', or 'delete'
    db_address: str, sqlalchemy address to the SNex2 db
    """
    logger.info(f'{action} for spectra. . .')
    spec_result = query_db_changes('spec', action, db_address=settings.SNEX1_DB_URL)
    logger.info(f'Total spectra changes {len([change.rowid for change in spec_result])}')
    for result in spec_result:
        try:
            id_ = result.rowid # The ID of the row in the spec table
            if action=='delete':
                #Look up the reduceddatum id from the datum_extra table
                rd_extra = ReducedDatumExtra.objects.filter(
                    data_type = 'spectroscopy',
                    value__snex_id = id_)
                for rde in rd_extra:
                    if rde.data_product:
                        dp = rde.data_product
                    elif rde.value.get('snex2_id',''):
                        rd_pk = rde.value.get('snex2_id','')
                        rd = ReducedDatum.objects.get(pk = rd_pk)
                        dp = rd.data_product
                
                dp.delete()

            else:
                spec_row = get_current_row(Spec, id_, db_address=settings.SNEX1_DB_URL) # The row corresponding to id_ in the spec table
                if not spec_row:
                    delete_row(Db_Changes, result.id, db_address=settings.SNEX1_DB_URL)
                    continue

                pipeline_id = spec_row.targetid
                time = '{} {}'.format(spec_row.dateobs, spec_row.ut)
                spec_filename = os.path.join(spec_row.filepath.replace(settings.SN_DIR, '/snex2/'), spec_row.filename.replace('.fits', '.ascii'))
                spec = read_spec(spec_filename)
                spec_groupid = spec_row.groupidcode
    
                with get_session(db_address=settings.SNEX1_DB_URL) as db_session:
                    standard_classification_row = db_session.query(Classifications).filter(Classifications.name=='Standard').first()
                    if standard_classification_row is not None:
                        standard_classification_id = standard_classification_row.id
                    else:
                        standard_classification_id = -1
                    standard_list = db_session.query(Targets).filter(Targets.classificationid==standard_classification_id)
                    standard_ids = [x.id for x in standard_list]
                if pipeline_id not in standard_ids:
                    target = Target.objects.get(pipeline_id = pipeline_id)
                    #created True means new DataProduct was made, created False is object already existed, like just "get"
                    data_product, dp_created = DataProduct.objects.get_or_create(
                        target = target, 
                        product_id = spec_row.filename.replace('.fits', '.ascii'),
                        data_product_type = 'spectroscopy')
                    
                    if dp_created:
                        data_product.data = data_product_path(data_product, spec_row.filename.replace('.fits', '.ascii'))
                        data_product.created = time
                        data_product.modified = time
                        data_product.featured = False
                        data_product.save()

                    reduced_datum, rd_created = ReducedDatum.objects.update_or_create(
                        target = target, 
                        data_product = data_product, 
                        data_type = 'spectroscopy', 
                        defaults = {
                            'value': spec,
                            'timestamp': time,
                            'source_name': '',
                            'source_location': '',
                        })

                    spec_extras = {}
                    for key in ['telescope', 'instrument', 'exptime', 'slit', 'airmass', 'reducer']:
                        if getattr(spec_row, key):
                            spec_extras[key] = getattr(spec_row, key)
                    spec_extras['snex_id'] = int(id_)
                    RDExtras_spec, rd_extras_created = ReducedDatumExtra.objects.update_or_create(
                        target = target,
                        data_product = data_product,
                        data_type='spectroscopy',
                        key='spec_extras',
                        value__snex_id = id_)

                    RDExtras_spec.value = spec_extras
                    RDExtras_spec.save()

                    logger.info(f'rd and extra made: {reduced_datum} {RDExtras_spec} for dataproduct: {data_product} and target {target}')

                    if spec_groupid is not None:
                        update_permissions(int(spec_groupid), 'view_reduceddatum', reduced_datum, snex1_groups) # everyone view reduceddatum
                        assign_perm('tom_dataproducts.view_dataproduct', Group.objects.get(name = "LCOGT"), data_product) # LCOGT group view and edit all dataproducts
                        assign_perm('tom_dataproducts.delete_dataproduct', Group.objects.get(name = "LCOGT"), data_product)

        except Exception as e:
            logger.exception(f"Failed to process spectrum for db_changes row {result.id} spec {result.rowid} with exception {e}")
            continue

        

def update_target(action, db_address=_SNEX2_DB):
    """
    Queries the Target table in the SNex2 db with any changes made to the Targets and Targetnames tables in the SNex1 db

    Parameters
    ----------
    action: str, one of 'update', 'insert', or 'delete'
    db_address: str, sqlalchemy address to the SNex2 db
    """
    logger.info('Updating Targets. . .')
    target_result = query_db_changes('targets', action, db_address=settings.SNEX1_DB_URL)
    name_result = query_db_changes('targetnames', action, db_address=settings.SNEX1_DB_URL)
    logger.info(f'Total target changes {len([change.rowid for change in target_result])}')
    logger.info(f'Total target name changes {len([change.rowid for change in name_result])}')

    for tresult in target_result:
        try:
            pipeline_id = tresult.rowid # The ID of the row in the targets table
            target_row = get_current_row(Targets, pipeline_id, db_address=settings.SNEX1_DB_URL) # The row corresponding to pipeline_id in the targets table

            t_ra = target_row.ra0
            t_dec = target_row.dec0
            t_modified = target_row.lastmodified
            t_created = target_row.datecreated
            if t_created is None:
                t_created = t_modified
            t_groupid = int(target_row.groupidcode)

            ### Get the name of the target
            with get_session(db_address=settings.SNEX1_DB_URL) as db_session:
                name_query = db_session.query(Target_Names).filter(Target_Names.targetid==target_row.id).first()
                if name_query is not None:
                    t_name = name_query.name
                else:
                    ### No name found, so target was created and deleted without being synced
                    continue
                db_session.commit()

            if action=='delete':
                Target.objects.delete(pipeline_id=pipeline_id)

            else:
                target, created = Target.objects.get_or_create(pipeline_id=pipeline_id)
                logger.info(f'target to sync: {pipeline_id}, target in snex2: {target}, created? {created}')

                if created:
                    target.name = t_name
                    target.ra = t_ra
                    target.dec = t_dec
                    target.modified = t_modified
                    target.created = t_created
                    target.type = 'SIDEREAL'
                    target.epoch = 2000
                    target.scheme = ''
                    target.permissions = 'PRIVATE'
                    target.save()
                    update_permissions(t_groupid, 'custom_code.change_target', target, snex1_groups)
                    update_permissions(t_groupid, 'custom_code.delete_target', target, snex1_groups)
                    update_permissions(t_groupid, 'custom_code.view_target', target, snex1_groups)

        except Exception as e:
            logger.exception(f"Failed to process spectrum for db_changes row {tresult.id} targets {tresult.rowid} with exception {e}")
            continue

        delete_row(Db_Changes, tresult.id, db_address=settings.SNEX1_DB_URL)
   
def run():
    """
    Migrates all changes from the SNex1 db to the SNex2 db,
    and afterwards deletes all the rows in the db_changes table
    """
    actions = ['delete', 'insert', 'update']
    for action in actions:
        logger.info(f'Running action: {action}')
        update_phot(action)
        logger.info('Done with photometry')
        update_spec(action)
        logger.info('Done with spectra')
        update_target(action, db_address = _SNEX2_DB)
        logger.info('Done with targets')
