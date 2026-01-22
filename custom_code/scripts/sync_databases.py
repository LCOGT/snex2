#!/usr/bin/env python

from sqlalchemy import create_engine, and_, update, insert, pool, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.sql import func

import json
from contextlib import contextmanager
import os
import datetime
from django.conf import settings
from tom_targets.models import Target
from tom_dataproducts.models import DataProduct, data_product_path, ReducedDatum
from custom_code.utils import powers_of_two
from custom_code.utils import update_permissions

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
Users = load_table('users', db_address=settings.SNEX1_DB_URL)

### And our SNex2 tables
Data_Product = load_table('tom_dataproducts_dataproduct', db_address=_SNEX2_DB)
Datum = load_table('tom_dataproducts_reduceddatum', db_address=_SNEX2_DB)
TargetTable = load_table('tom_targets_basetarget', db_address=_SNEX2_DB)
Target_Extra = load_table('tom_targets_targetextra', db_address=_SNEX2_DB)
Targetname = load_table('tom_targets_targetname', db_address=_SNEX2_DB)
Auth_Group = load_table('auth_group', db_address=_SNEX2_DB)
Group_Perm = load_table('guardian_groupobjectpermission', db_address=_SNEX2_DB)
Datum_Extra = load_table('custom_code_reduceddatumextra', db_address=_SNEX2_DB)
Auth_User = load_table('auth_user', db_address=_SNEX2_DB)
Auth_User_Group = load_table('auth_user_groups', db_address=_SNEX2_DB)

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
    #table_dict = {'photlco': Photlco, 'spec': Spec, 'targets': Targets, 'targetnames': Target_Names}
    with get_session(db_address=db_address) as db_session:
        criteria = and_(Db_Changes.tablename==table, Db_Changes.action==action)
        record = db_session.query(Db_Changes).filter(criteria)#.order_by(Db_Changes.id.desc()).all()
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
        #logger.info("in get_current_row function...")
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


def update_phot(action, db_address=_SNEX2_DB):
    """
    Queries the ReducedDatum table in the SNex2 db with any changes made to the Photlco table in the SNex1 db

    Parameters
    ----------
    action: str, one of 'update', 'insert', or 'delete'
    db_address: str, sqlalchemy address to the SNex2 db
    """
    logger.info('Updating Photometry. . .')
    phot_result = query_db_changes('photlco', action, db_address=settings.SNEX1_DB_URL)
    logger.info(f'Total photometry changes {len([change.rowid for change in phot_result])}')
    #import pdb
    #pdb.set_trace()
    i = 0
    logger.info("photlco:" + str(Photlco))
    for result in phot_result:
        logger.info("result rowid:" + str(result.rowid))
        i += 1
        logger.info("i=" + str(i))
        try:
            id_ = result.rowid # The ID of the row in the photlco table
            #logger.info("before phot_row defined")
            # AttributeError: 'NoneType' object has no attribute 'id' error on phot_row definition
            # so Photlco table has no attribute 'id' -- did the name of column get changed?
            phot_row = get_current_row(Photlco, id_, db_address=settings.SNEX1_DB_URL) # The row corresponding to id_ in the photlco table    
            #targetid = phot_row.targetid
            #logger.info("line 181")
            if action=='delete':
                logger.info("action = delete")
                #Look up the dataproductid from the datum_extra table
                with get_session(db_address=db_address) as db_session:
                    
                    # #snex2_id_query = db_session.query(Datum).filter(and_(Datum.target_id==targetid, Datum.data_type=='photometry')).all()
                    # snex2_id_query = db_session.query(Datum).filter(Datum.data_type=='photometry').order_by(Datum.id.desc()).all()
                    # for snex2_row in snex2_id_query:
                    #     value = snex2_row.value
                    #     if type(value) == str:
                    #         value = json.loads(snex2_row.value)
                    #     if id_ == value.get('snex_id', ''):
                    #         db_session.delete(snex2_row)
                    #         break
                    # t = Target.objects.filter(px=targetid)[0]
                    # r = ReducedDatum.objects.filter(target=t,value={'snex_id': id_})
                    # if len(r) > 0:
                    #     r[0].delete()

                    snex2_id_query = db_session.query(Datum).filter(
                        Datum.value['snex_id'].astext == str(id_)
                    ).first()
                    if snex2_id_query is not None:
                        db_session.delete(snex2_id_query)
                    db_session.commit()

                    #snex2_id_query = db_session.query(Datum_Extra).filter(and_(Datum_Extra.snex_id==id_, Datum_Extra.data_type=='photometry')).first()
                    #if snex2_id_query is not None: #Is none if row gets inserted and deleted in same 5 min block
                    #    #snex2_id = snex2_id_query.reduced_datum_id
                    #    #datum = db_session.query(Datum).filter(Datum.id==snex2_id).first()
                    #    #db_session.delete(datum)
                    #db_session.commit()

                     #Delete all other rows corresponding to this dataproduct in the db_changes table
                with get_session(db_address=settings.SNEX1_DB_URL) as db_session:
                    all_other_rows = db_session.query(Db_Changes).filter(and_(Db_Changes.tablename=='photlco', Db_Changes.rowid==id_))
                    for row in all_other_rows:
                        db_session.delete(row)
                    db_session.commit()
                    
            else:
                logger.info("action is not delete")
                targetid = phot_row.targetid
                dobs = phot_row.dateobs
                tobs = phot_row.ut
                #logger.info("targetid defined")
                if tobs is None:
                    tobs = '00:00:00'
                if dobs is None:
                    dobs = datetime.datetime.today().strftime('%Y-%m-%d')
                time = '{} {}'.format(dobs, tobs) 
                
                if int(phot_row.mag) != 9999:
                    #logger.info("magnitude != 9999")
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
                    #logger.info("magnitude == 9999")
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
                    logger.info("got session")
                if targetid not in standard_ids and int(phot_row.filetype) in (1, 3):
                    if 'background_subtracted' in phot.keys():
                        logger.info("background subtracted key exists")
                        data_point = ReducedDatum.objects.filter(target_id=targetid, timestamp=time, data_type='photometry', value__snex_id=phot['snex_id'], value__background_subtracted=phot['background_subtracted'])
                        #if len(data_point) == 2:
                        #    if data_point[0].value == data_point[1].value:
                        #        data_point[0].delete()
                        if len(data_point) > 1:
                            logger.info(f"{len(data_point)} data points, trying to consolidate")
                            
                            for x, point in enumerate(data_point):
                                try:
                                    print(x)
                                    if point.value == data_point[x+1].value:
                                        point.delete()
                                        logger.info(f"deleted point {x}")
                                except:
                                    logger.info("x+1 indexing for multiple data points failed")
                                    continue

                        data_point = ReducedDatum.objects.filter(target_id=targetid, timestamp=time, data_type='photometry', value__snex_id=phot['snex_id'], value__background_subtracted=phot['background_subtracted']).first()

                    else:
                        data_point = ReducedDatum.objects.filter(target_id=targetid, timestamp=time, data_type='photometry', value__snex_id=phot['snex_id'])
                        #if len(data_point) == 2:
                        #    if data_point[0].value == data_point[1].value:
                        #        data_point[0].delete()
                        if len(data_point) > 1:
                            logger.info(f"{len(data_point)} data points, trying to consolidate")
                            
                            for x, point in enumerate(data_point):
                                try:
                                    print(x)
                                    if point.value == data_point[x+1].value:
                                        point.delete()
                                        logger.info(f"deleted point {x}")
                                except:
                                    logger.info("x+1 indexing for multiple data points failed")
                                    continue

                        data_point = ReducedDatum.objects.filter(target_id=targetid, timestamp=time, data_type='photometry', value__snex_id=phot['snex_id']).first()
                        #taking the first one of the list

                    #update
                    #logger.info("line 218 update")
                    if data_point:
                        logger.info(f'Existing Phot point for target {targetid}: {data_point}, timestamp:{time}, value: {data_point.value}')
                        data_point.value = phot
                        data_point.source_name = ''
                        data_point.source_location = ''
                        #logger.info("Before data_point.save()")
                        data_point.save()

                    #insert
                    else:
                        #logger.info("line 290 insert")
                        data_point = ReducedDatum.objects.create(target_id=targetid, timestamp=time, data_type='photometry', value=phot, source_name='', source_location='')

                    if phot_groupid is not None:
                        #logger.info("phot_groupid is not None")
                        update_permissions(int(phot_groupid), 'view_reduceddatum', data_point, snex1_groups)
                    db_session.commit()
                delete_row(Db_Changes, result.id, db_address=settings.SNEX1_DB_URL)

        except:
            #logger.info("except, line 302")
            raise #continue


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


def update_spec(action, db_address=_SNEX2_DB):
    """
    Queries the ReducedDatum table in the SNex2 db with any changes made to the Spec table in the SNex1 db

    Parameters
    ----------
    action: str, one of 'update', 'insert', or 'delete'
    db_address: str, sqlalchemy address to the SNex2 db
    """
    logger.info('Updating Spectra. . .')
    spec_result = query_db_changes('spec', action, db_address=settings.SNEX1_DB_URL)
    logger.info(f'Total spectra changes {len([change.rowid for change in spec_result])}')
    for result in spec_result:
        try:
            id_ = result.rowid # The ID of the row in the spec table
            # target_id = result.targetid
            if action=='delete':
                #Look up the dataproductid from the datum_extra table
                with get_session(db_address=db_address) as db_session:
                    
                    #snex2_id_query = db_session.query(Datum).filter(and_(Datum.target_id==targetid, Datum.data_type=='spectroscopy')).all()
                    #for snex2_row in snex2_id_query:
                    #    value = json.loads(snex2_row.value)
                    #    if id_ == value.get('snex_id', ''):
                    #        db_session.delete(snex2_row)
                    #        break
                    #db_session.commit()

                    snex2_id_query = db_session.query(Datum_Extra).filter(and_(Datum_Extra.data_type=='spectroscopy', Datum_Extra.key=='snex_id')).all()
                    # t = Target.objects.filter(pk=target_id)
                    # ReducedDatumExtra.objects.filter(target=t,data_type='spectroscopy',snex_id=id_)
                    for snex2_row in snex2_id_query:
                        value = json.loads(snex2_row.value)
                        if id_ == value.get('snex_id', ''):
                            snex2_id = value.get('snex2_id', '')
                            spec = db_session.query(Datum).filter(and_(Datum.data_type=='spectroscopy', Datum.id==snex2_id)).first()
                            if not spec.data_product_id:
                                db_session.delete(spec)
                            else: # Delete the associated DataProduct with the spectrum
                                data_product_id = spec.data_product_id
                                db_session.delete(spec)
                                db_session.query(Data_Product).filter(Data_Product.id == data_product_id).delete()
                            
                            break
                    db_session.commit()

            else:
                spec_row = get_current_row(Spec, id_, db_address=settings.SNEX1_DB_URL) # The row corresponding to id_ in the spec table

                if not spec_row:
                    delete_row(Db_Changes, result.id, db_address=settings.SNEX1_DB_URL)
                    continue

                targetid = spec_row.targetid
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
                if targetid not in standard_ids:
                    if action=='update':
                        logger.info("action: update")
                        #snex2_id_query = db_session.query(Datum).filter(and_(Datum.target_id==targetid, Datum.data_type=='spectroscopy')).all()
                        #for snex2_row in snex2_id_query:
                        #    value = json.loads(snex2_row.value)
                        #    if id_ == value.get('snex_id', ''):
                        #        snex2_row.update({'target_id': targetid, 'timestamp': time, 'value': spec, 'data_type': 'spectroscopy', 'source_name': '', 'source_location': ''})
                        #        break
                        with get_session(db_address=db_address) as db_session:
                            snex2_id_query = db_session.query(Datum_Extra).filter(and_(Datum_Extra.target_id==targetid, Datum_Extra.key=='snex_id', Datum_Extra.data_type=='spectroscopy')).all()
                            for snex2_row in snex2_id_query:
                                value = json.loads(snex2_row.value)
                                if id_ == value.get('snex_id', ''):
                                    snex2_id = value.get('snex2_id', '')

                                    original_rd = ReducedDatum.objects.filter(id=snex2_id).first()

                                    logger.info(f"Original ReducedDatum: {original_rd}")

                                    find_dup_query = ReducedDatum.objects.filter(target_id=original_rd.target_id, data_type='spectroscopy',timestamp=original_rd.timestamp,value=original_rd.value)
                                    
                                    logger.info(f"Looking for duplicates:{find_dup_query}")

                                    if len(find_dup_query) > 1:

                                        logger.info(f"Duplicate found. {len(find_dup_query)} data points, trying to consolidate")
                                        
                                        for point in find_dup_query:
                                            if (point.value == original_rd.value) & (point.timestamp == original_rd.timestamp):
                                                if point.id != snex2_id:
                                                    logger.info(f"Deleted extraneous point {point}")
                                                    point.delete()
                                                else:
                                                    logger.info(f"Point {point} not deleted")
                                           
                                    data_point = ReducedDatum.objects.get(id=snex2_id)
                                    logger.info(f"single data_point:{data_point}")

                                    data_point.target_id = targetid

                                    data_point.timestamp = time

                                    data_point.value = spec
                                    data_point.data_type = 'spectroscopy'
                                    data_point.source_name = ''
                                    data_point.source_location = ''

                                    data_point.save()
                                    logger.info("data_point has been saved")
                                    if spec_groupid is not None:
                                        update_permissions(int(spec_groupid), 'view_reduceddatum', data_point, snex1_groups) #View reduceddatum
                                    break

                    elif action=='insert':
                        with get_session(db_address=db_address) as db_session:
                            # First create the dataproduct for this spectra linking to the ascii file
                            newdp = Data_Product(
                                target_id=targetid, 
                                product_id=spec_row.filename.replace('.fits', '.ascii'), 
                                data_product_type='spectroscopy', 
                                data=spec_row.filename.replace('.fits', '.ascii'),
                                extra_data='',
                                created=time,
                                modified=time,
                                featured=False)
                            db_session.add(newdp)
                            db_session.flush()

                        data_point, created = ReducedDatum.objects.get_or_create(target_id=targetid, data_product_id=newdp.id, timestamp=time, value=spec, data_type='spectroscopy', source_name='', source_location='')
                        # Then create the reduced datum referencing the data product
                        #newspec = Datum(target_id=targetid, timestamp=time, value=spec, data_type='spectroscopy', source_name='', source_location='')


                        if spec_groupid is not None:
                            update_permissions(int(spec_groupid), 'view_reduceddatum', data_point, snex1_groups) #View reduceddatum

                        #newspec_extra = Datum_Extra(snex_id=int(id_), reduced_datum_id=int(newspec.id), data_type='spectroscopy', key='', value='')
                        #db_session.add(newspec_extra)

                        newspec_extra_value = json.dumps({'snex_id': int(id_), 'snex2_id': int(data_point.id)})
                        newspec_extra = Datum_Extra(target_id=targetid, data_type='spectroscopy', key='snex_id', value=newspec_extra_value)
                        db_session.add(newspec_extra)

                        spec_extras = {}
                        for key in ['telescope', 'instrument', 'exptime', 'slit', 'airmass', 'reducer']:
                            if getattr(spec_row, key):
                                spec_extras[key] = getattr(spec_row, key)
                        spec_extras['snex_id'] = int(id_)
                        spec_extras_row = Datum_Extra(data_type='spectroscopy', key='spec_extras', value=json.dumps(spec_extras), target_id=targetid)
                        db_session.add(spec_extras_row)

                    db_session.commit()
                    if action == 'insert':
                        # Finally update the newly created dataproduct using the Django path
                        # This is normally done automatically using the Django ORM,
                        # but since we're using sqlalchemy we have to do it manually
                        snex2_dp = DataProduct.objects.get(id=newdp.id)
                        snex2_dp.data = data_product_path(snex2_dp, snex2_dp.data)
                        snex2_dp.save()
                        
            delete_row(Db_Changes, result.id, db_address=settings.SNEX1_DB_URL)

        except:
            raise #continue


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
            target_id = tresult.rowid # The ID of the row in the targets table
            target_row = get_current_row(Targets, target_id, db_address=settings.SNEX1_DB_URL) # The row corresponding to target_id in the targets table

            t_ra = target_row.ra0
            t_dec = target_row.dec0
            t_modified = target_row.lastmodified
            t_created = target_row.datecreated
            if t_created is None:
                t_created = t_modified
            t_groupid = int(target_row.groupidcode)
            t_redshift = target_row.redshift

            class_id = target_row.classificationid
            if class_id is not None:
                class_name = get_current_row(Classifications, class_id, db_address=settings.SNEX1_DB_URL).name # Get the classification from the classifications table based on the classification id in the targets table (wtf)
                

            ### Get the name of the target
            with get_session(db_address=settings.SNEX1_DB_URL) as db_session:
                name_query = db_session.query(Target_Names).filter(Target_Names.targetid==target_row.id).first()
                if name_query is not None:
                    t_name = name_query.name
                else:
                    ### No name found, so target was created and deleted without being synced
                    continue
                db_session.commit()

            with get_session(db_address=db_address) as db_session:
                if action=='update':
                    target = Target.objects.get(pk=target_id)
                    logger.info(f'updating target: {target}')
                    # the following could be the same as the insert action, not sure if necessary to have
                    #   as a separate code block
                    Target.objects.filter(pk=target_id).update(ra=t_ra,
                                                               dec=t_dec,
                                                               modified=t_modified,
                                                               created=t_created,
                                                               type='SIDEREAL',
                                                               epoch=2000,
                                                               scheme='')
                    update_permissions(t_groupid, 'change_target', target, snex1_groups)
                    update_permissions(t_groupid, 'delete_target', target, snex1_groups)
                    update_permissions(t_groupid, 'view_target', target, snex1_groups)
                elif action=='insert':
                    target, created = Target.objects.get_or_create(id=target_id)
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
                    if 'postgresql' in db_address:
                        db_session.execute(select(func.setval('tom_targets_target_id_seq', target_id)))
                    update_permissions(t_groupid, 'change_target', target, snex1_groups)
                    update_permissions(t_groupid, 'delete_target', target, snex1_groups)
                    update_permissions(t_groupid, 'view_target', target, snex1_groups)
                elif action=='delete':
                    Target.objects.delete(id=target_id)

                db_session.commit()
            delete_row(Db_Changes, tresult.id, db_address=settings.SNEX1_DB_URL)

        except:
            raise #continue

    for nresult in name_result:
        try:
            name_id = nresult.rowid # The ID of the row in the targetnames table
            name_row = get_current_row(Target_Names, name_id, db_address=settings.SNEX1_DB_URL) # The row corresponding to name_id in the targetnames table

            if action!='delete':
                n_id = name_row.targetid
                t_name = name_row.name
                
                with get_session(db_address=settings.SNEX1_DB_URL) as db_session:
                    standard_classification_row = db_session.query(Classifications).filter(Classifications.name=='Standard').first()
                    if standard_classification_row is not None:
                        standard_classification_id = standard_classification_row.id
                    else:
                        standard_classification_id = -1
                    standard_list = db_session.query(Targets).filter(Targets.classificationid==standard_classification_id)
                    standard_ids = [x.id for x in standard_list]

                if n_id not in standard_ids:

                    with get_session(db_address=db_address) as db_session:
                        targetname_criteria = and_(Targetname.name==t_name, Targetname.target_id==n_id)
                        if action=='update':
                            db_session.query(TargetTable).filter(TargetTable.id==n_id).update({'name': t_name})
                            db_session.query(Targetname).filter(targetname_criteria).update({'name': t_name})

                        elif action=='insert':
                            existing_name = db_session.query(Targetname).filter(Targetname.name==t_name, Targetname.target_id==n_id).first()
                            if not existing_name:
                                db_session.add(Targetname(name=t_name, target_id=n_id, created=datetime.datetime.utcnow(), modified=datetime.datetime.utcnow()))

                    db_session.commit()
            
            #TODO: Delete currently doesn't work because targetname_criteria doesn't work
            #      need to figure out how to find the name that was deleted from SNEx1

            #elif action=='delete': 
            #    with get_session(db_address=db_address) as db_session:
            #        targetname_criteria = and_(Targetname.name==t_name, Targetname.target_id==n_id)
            #        name_delete = db_session.query(Targetname).filter(targetname_criteria).first()
            #        db_session.delete(name_delete)
            #    db_session.commit()

            delete_row(Db_Changes, nresult.id, db_address=settings.SNEX1_DB_URL)
        
        except:
            raise #continue


def update_target_extra(action, db_address=_SNEX2_DB):
    """
    Queries the Targetextra table in the SNex2 db with any changes made to the Targets table, along with info from the Classifications table, in the SNex1 db

    Parameters
    ----------
    action: str, one of 'update', 'insert', or 'delete'
    db_address: str, sqlalchemy address to the SNex2 db
    """
    target_result = query_db_changes('targets', action, db_address=settings.SNEX1_DB_URL)

    for tresult in target_result:
        try:
            target_id = tresult.rowid # The ID of the row in the targets table
            target_row = get_current_row(Targets, target_id, db_address=settings.SNEX1_DB_URL) # The row corresponding to target_id in the targets table

            #t_id = target_row.id
            value = target_row.redshift
            if value is not None:
                with get_session(db_address=db_address) as db_session:
                    z_criteria = and_(Target_Extra.target_id==target_id, Target_Extra.key=='redshift') # Criteria for updating the redshift info in the targetextra table
                    
                    if action=='update' or action=='insert':
                        if db_session.query(Target_Extra).filter(z_criteria).first() is not None:
                            db_session.query(Target_Extra).filter(z_criteria).update({'value': str(value), 'float_value': float(value)})
                        else:
                            db_session.add(Target_Extra(target_id=target_id, key='redshift', value=str(value), float_value=float(value)))

                    #Don't think the below are necessary, but need to double check
                    #elif action=='insert':
                        #db_session.add(Target_Extra(target_id=target_id, key='redshift', value=str(value), float_value=float(value)))
                    
                    elif action=='delete':
                        db_session.query(Target_Extra).filter(z_criteria).delete()
                    db_session.commit()

            class_id = target_row.classificationid
            if class_id is not None:
                class_name = get_current_row(Classifications, class_id, db_address=settings.SNEX1_DB_URL).name # Get the classification from the classifications table based on the classification id in the targets table (wtf)
                with get_session(db_address=db_address) as db_session:
                    c_criteria = and_(Target_Extra.target_id==target_id, Target_Extra.key=='classification') # Criteria for updating the classification info in the targetextra table
                    if action=='update':
                        if db_session.query(Target_Extra).filter(c_criteria).first() is not None:
                            db_session.query(Target_Extra).filter(c_criteria).update({'value': class_name})
                        else:
                            db_session.add(Target_Extra(target_id=target_id, key='classification', value=class_name))

                    elif action=='insert':
                        db_session.add(Target_Extra(target_id=target_id, key='classification', value=class_name))

                    elif action=='delete':
                        db_session.query(Target_Extra).filter(c_criteria).delete()

                    db_session.commit()
            delete_row(Db_Changes, tresult.id, db_address=settings.SNEX1_DB_URL)

        except:
            raise #continue


def update_users(action, db_address=_SNEX2_DB):
    """
    Update the snex 2 db when a users registers or changes their username/password in the
    snex 1 db.

    Parameters
    ----------
    action: str, action that was done on the users table ['update', 'insert', 'delete']
    """
    logger.info('Updating Users. . .')
    user_changes = query_db_changes('users', action, db_address=settings.SNEX1_DB_URL)
    logger.info(f'Total user changes {len([change.rowid for change in user_changes])}')
    for change in user_changes:
        try:
            row_id = change.rowid
            user_row = get_current_row(Users, row_id, db_address=settings.SNEX1_DB_URL)
            if action == 'delete':
                old_username = change.locator
                with get_session(db_address=db_address) as db_session:
                    db_session.query(Auth_User).filter(Auth_User.username == old_username).delete()
                    db_session.commit()

            elif action == 'insert':
                with get_session(db_address=db_address) as db_session:

                    user = (
                        db_session.query(Auth_User)
                        .filter(Auth_User.username == user_row.name)
                        .one_or_none()
                    )

                    if user:
                        logger.info(f'User already exists, updating: {user.username}')
                        user.password = 'crypt$$' + user_row.pw
                        user.first_name = user_row.firstname
                        user.last_name = user_row.lastname
                        user.email = user_row.email
                    else:
                        logger.info(f'Creating new user: {user_row.name}')
                        user = Auth_User(
                            username=user_row.name,
                            password='crypt$$' + user_row.pw,
                            first_name=user_row.firstname,
                            last_name=user_row.lastname,
                            email=user_row.email,
                            is_staff=False,
                            is_active=True,
                            is_superuser=False,
                            date_joined=user_row.datecreated,
                        )
                        db_session.add(user)
                        db_session.flush()
                        
                    affiliated_group_idcodes = powers_of_two(user_row.groupidcode)

                    for g_name, g_id in snex1_groups.items():
                        if g_id in affiliated_group_idcodes:
                            snex2_group = (
                                db_session.query(Auth_Group)
                                .filter(Auth_Group.name == g_name)
                                .one()
                            )

                            exists = (
                                db_session.query(Auth_User_Group)
                                .filter(
                                    Auth_User_Group.user_id == user.id,
                                    Auth_User_Group.group_id == snex2_group.id,
                                )
                                .first()
                            )

                            if not exists:
                                db_session.add(
                                    Auth_User_Group(
                                        user_id=user.id,
                                        group_id=snex2_group.id,
                                    )
                                )

                    db_session.commit()


            elif action == 'update':
                old_username = change.locator
                with get_session(db_address=db_address) as db_session:
                    db_session.query(Auth_User).filter(
                        Auth_User.username==old_username
                    ).update(
                        {'username': user_row.name,
                         'password': 'crypt$$'+user_row.pw,
                         'first_name': user_row.firstname,
                         'last_name': user_row.lastname,
                         'email': user_row.email}
                    )
                    db_session.commit()

            delete_row(Db_Changes, change.id, db_address=settings.SNEX1_DB_URL)

        except:
            raise


def update_groups(action, db_address=_SNEX2_DB):
    logger.info('Updating Groups. . .')
    group_changes = query_db_changes('groups', action, db_address=settings.SNEX1_DB_URL)
    logger.info(f'Total group changes {len([change.rowid for change in group_changes])}')
    for change in group_changes:
        row_id = change.rowid
        group_row = get_current_row(Groups, row_id, db_address=settings.SNEX1_DB_URL)
        if action == 'delete':
            old_group_name = change.locator
            with get_session(db_address=db_address) as db_session:
                db_session.query(Auth_Group).filter(Auth_Group.name == old_group_name).delete()
                db_session.commit()

        elif action == 'insert':
            with get_session(db_address=db_address) as db_session:
                new_group = Auth_Group(name=group_row.name)
                db_session.add(new_group)
                db_session.commit()

        elif action == 'update':
            old_group_name = change.locator
            with get_session(db_address=db_address) as db_session:
                db_session.query(Auth_Group).filter(
                    Auth_Group.name == old_group_name
                ).update(
                    {'name': group_row.name}
                )
                db_session.commit()

        delete_row(Db_Changes, change.id, db_address=settings.SNEX1_DB_URL)


def run():
    """
    Migrates all changes from the SNex1 db to the SNex2 db,
    and afterwards deletes all the rows in the db_changes table
    """
    actions = ['delete', 'insert', 'update']
    for action in actions:
        logger.info(f'Updating for action: {action}')
        update_groups(action, db_address=_SNEX2_DB)
        logger.info('Done updating groups')
        update_users(action, db_address=_SNEX2_DB)
        logger.info('Done updating users')
        update_target(action, db_address=_SNEX2_DB)
        logger.info('Done updating targets')
        update_phot(action, db_address=_SNEX2_DB)
        logger.info('Done updating photometry')
        update_spec(action, db_address=_SNEX2_DB)
        logger.info('Done updating spectra')
