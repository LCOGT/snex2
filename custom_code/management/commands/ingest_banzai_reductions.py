from io import BytesIO
from django.core.management.base import BaseCommand
from django.http import FileResponse
from tom_dataproducts.models import ReducedDatum, DataProduct, ObservationRecord
from tom_targets.sharing import continuous_share_data
from tom_dataproducts.processors.data_serializers import SpectrumSerializer
from custom_code.models import DataProductExtra, ReducedDatumSpecExtra 
from custom_code.processors.spectroscopy_processor import process_fits_file
from astropy.io import fits
import base64
import requests
import hashlib
from datetime import datetime, timedelta


from django.conf import settings


import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Ingest banzai-floyds reduced spectra from the last 7 days into ReducedDatum table'

    
    def get_hash(self, basename):
        token = settings.FACILITIES['LCO']['api_key']
        url = settings.FACILITIES['LCO']['archive_url']

        # Get version hash for frame
        results = requests.get(url,
                            headers={'Authorization': f'Token {token}'}, 
                            params={'basename_exact': basename, 'include_related_frames': False}).json()["results"]
        
        if not results:
            logger.info(f"get_hash: No results for {basename}")
        
        version = results[0]['version_set'][0].get('md5', False)
        data = requests.get(results[0]["url"]).content
        #file =  FileResponse(BytesIO(data),filename=basename+'.fits')
        
        if not version:
            # hash = hashlib.md5()
            # with open(file, "rb") as f:
            #     for chunk in iter(lambda: f.read(8192), b""):
            #         hash.update(chunk)
            version = hashlib.md5(data).hexdigest()
        return version, data


    def make_ReducedDatums(self, dp, basename, dp_extras, data):
        file =  BytesIO(data)
        if 'e91-1d' in basename:
            logger.info(f"Making reduced datum for 1d file {basename}")
            file.seek(0)
            with fits.open(BytesIO(data)) as f:
                try:
                    logger.info(f"Trying first extension")
                    reducer = f[0].header['REDUCER']
                except Exception as e:
                    logger.info(f"{e}: trying second header extension ")
                    reducer = f[1].header['REDUCER']
            try:
                file.seek(0)
                logger.info(f"Found reducer for 1d, processing file..")
                spectrum, dp_extras, date_obs = process_fits_file(file, dp_extras)
                logger.info("Processed file")
                serialized_spectrum = SpectrumSerializer().serialize(spectrum)
                logger.info(f"Serialized spectrum, making reduced_datum")
                reduced_datum= ReducedDatum.objects.create(target=dp.target, data_product=dp, data_type=dp.data_product_type,
                                    timestamp=date_obs, value=serialized_spectrum)
            except Exception as e:
                logger.info(f"Couldn't make 1d reduced datum: {e}")
        elif 'e91-2d' in basename:
            with fits.open(BytesIO(data)) as f:

                try:
                    logger.info(f"Trying first extension")
                    reducer = f[0].header['REDUCER']
                    value = f[0].data
                    date_obs = f[0].header['DATE-OBS']
                except Exception as e:
                    logger.info(f"{e}: Trying second header extension ")
                    reducer = f[1].header['REDUCER']
                    value = f[1].data
                    #logger.info(f"value: {value}")
                    date_obs = f[1].header['DATE-OBS']
            try:
                buf = BytesIO()
                with fits.open(BytesIO(data)) as f:
                    f.writeto(buf)
                fits_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

                reduced_datum = ReducedDatum.objects.create(
                    target=dp.target,
                    data_product=dp,
                    data_type=dp.data_product_type,
                    timestamp=date_obs,
                    value={'fits_data': fits_b64}
)
            except Exception as e:
                logger.info(f"Couldn't make 2d reduced datum : {e}")

        continuous_share_data(dp.target, reduced_datum) # ???? make sure this works
        return reduced_datum, reducer
    
    def get_metadata(self, authtoken={}, limit=None, **kwargs):
        '''Get the list of files meeting criteria in kwargs'''

        if (kwargs.get('start') is not None) & (kwargs.get('end') is not None):
            start = start[0:4] + '-' + start[4:6] + '-' + start[6:8]
            end = end[0:4] + '-' + end[4:6] + '-' + end[6:8]
        else:
            now = datetime.utcnow()
            start = datetime.strftime(now - timedelta(days=7), '%Y-%m-%d') # check for data in the last week
            end = datetime.strftime(now, '%Y-%m-%d %H:%M:%S')
            kwargs['start'] = start
            kwargs['end'] = end

        url = 'https://archive-api.lco.global/frames/?' + '&'.join(
                [key + '=' + str(val) for key, val in kwargs.items() if val is not None])
        url = url.replace('False', 'false')
        url = url.replace('True', 'true')
        logger.info(url)

        response = requests.get(url, headers=authtoken, stream=True).json()
        frames = response['results']
        while response['next'] and (limit is None or len(frames) < limit):
            logger.info(response['next'])
            response = requests.get(response['next'], headers=authtoken, stream=True).json()
            frames += response['results']
        return frames[:limit]
    
    def add_arguments(self, parser):
        parser.add_argument("-l", "--limit", type=int, help="maximum number of frames to return")
        parser.add_argument("-S", "--site", choices=['bpl', 'coj', 'cpt', 'elp', 'lsc', 'ogg', 'sqa', 'tfn'])
        parser.add_argument("-T", "--telescope", choices=['0m4a', '0m4b', '0m4c', '0m8a', '1m0a', '2m0a'])
        parser.add_argument("-I", "--instrument")
        parser.add_argument("-f", "--filter", choices=['up', 'gp', 'rp', 'ip', 'zs', 'U', 'B', 'V', 'R', 'I'])
        parser.add_argument("-P", "--proposal", help="proposal ID (PROPID in the header)")
        parser.add_argument("-n", "--name", help="target name")
        parser.add_argument("-s", "--start", help="start date")
        parser.add_argument("-e", "--end", help="end date")
        parser.add_argument("-c", "--coords", nargs=2, help="target coordinates in degrees, space separated")
        parser.add_argument("-t", "--obstype", choices=['ARC', 'BIAS', 'CATALOG', 'DARK', 'EXPERIMENTAL',
                                            'EXPOSE', 'LAMPFLAT', 'SKYFLAT', 'SPECTRUM', 'STANDARD'])
        parser.add_argument("--public", action='store_true', help="include public data")
        parser.add_argument("-g", "--groups", help="groups")

    

    def handle(self, *args, **options):

        token = settings.FACILITIES['LCO']['api_key']
        authtoken = {'Authorization': 'Token ' + token}

        # raw_frames = self.get_metadata(authtoken, limit=args.limit, SITEID=args.site, TELID=args.telescope,
        #                   INSTRUME=args.instrument, FILTER=args.filter, PROPID=args.proposal, OBJECT=args.name,
        #                   start=args.start, end=args.end, OBSTYPE=args.obstype, RLEVEL=0, include_thumbnails=True, include_related_frames = False,
        #                   public=args.public, covers='POINT({} {})'.format(*args.coords) if args.coords else None)
        

        raw_frames = self.get_metadata(
            authtoken,
            limit=options['limit'],
            basename='ogg2m001-en06-20260428-0001',
            SITEID=options['site'],
            TELID=options['telescope'],
            INSTRUME=options['instrument'],
            FILTER=options['filter'],
            PROPID=options['proposal'],
            OBJECT=options['name'],
            start=options['start'],
            end=options['end'],
            OBSTYPE=options['obstype'],
            RLEVEL=0,
            include_thumbnails=False,
            include_related_frames=False,
            public=options['public'],
            covers='POINT({} {})'.format(*options['coords']) if options['coords'] else None
        )
    
        for frame in raw_frames:
            observation_id = 677447526 #frame['observation_id']
            obs_record = ObservationRecord.objects.get(observation_id = observation_id) 
            try:
                target = obs_record.target
            except Exception as e:
                logger.info(f"{e} Target doesn't exist.")

                headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}

                targets_url = 'http://127.0.0.1:8889/api/targets/' # TEST
        
                data = requests.get(frame[0]["url"]).content
                file =  FileResponse(BytesIO(data))
                with fits.open(file) as f:
                    RA = f[1].header['RA']
                    DEC = f[1].header['DEC']

                params = {
                    "name": frame['target_name'],
                    "type": "SIDEREAL",
                    "permissions": "PRIVATE",
                    "ra": RA,
                    "dec": DEC,
                    "scheme": "",
                    "observing_run_priority": 0.0,
                    "groups": settings.DEFAULT_GROUPS,
                }
                if args.groups is not None:
                    params['groups'] = args.groups


                response = requests.post(targets_url, auth = authtoken, headers = headers, json = params)
                logger.info(f"{response},{response.url}, {response.text}")

        


            data_product, created = DataProduct.objects.get_or_create(product_id = frame['id'], 
                                                                      data_product_type = 'spectroscopy', 
                                                                      target = target,
                                                                      extra_data = frame['basename'],
                                                                      observation_record = obs_record
                                                                      )
            data_product.data.name = frame['basename']
            data_product.save()
            logger.info(f"Created {created}, data_product {data_product.pk}")
  
            reduced_base1d = frame['basename'].replace('e00', 'e91-1d')
            reduced_base2d = frame['basename'].replace('e00', 'e91-2d')
            
            if created: # New raw frame observation, new dp - no reduceddatum currently associated
                logger.info(f"DataProduct created")
                dpextra_value = {
                    'telescope':  frame.get('TELID', ''),
                    'instrument': frame.get('INSTRUME', ''),
                    'site':       frame.get('SITEID', ''),
                    'exptime':    frame.get('EXPTIME', ''),
                    'airmass':    frame.get('AIRMASS', ''),
                }
                
                data_product_extra, _ = DataProductExtra.objects.get_or_create(
                            data_product = data_product,
                            key = 'spec_extras',
                            value = dpextra_value,
                            viewed = False
                        )   
               
                reduced_base1d = frame['basename'].replace('e00', 'e91-1d')
                reduced_base2d = frame['basename'].replace('e00', 'e91-2d')

                v1, data1 = self.get_hash(reduced_base1d)
                v1_list = ReducedDatumSpecExtra.objects.filter(reduced_datum__data_product = data_product, version = v1) # target is redundant
                if v1_list.count() == 0: 
                    logger.info(f"Hash {v1} is unique, creating reduced_datums ")
                    rd1d, reducer = self.make_ReducedDatums(data_product, reduced_base1d, dpextra_value, data1)

                    rde_1d, _ = ReducedDatumSpecExtra.objects.get_or_create(reduced_datum = rd1d, 
                                                                    reducer = reducer, 
                                                                    show = True,
                                                                    version = v1)
                    
                
                v2, data2 = self.get_hash(reduced_base2d)
                v2_list = ReducedDatumSpecExtra.objects.filter(reduced_datum__data_product = data_product, version = v2)
                if v2_list.count() == 0: 
                    rd2d, reducer = self.make_ReducedDatums(data_product, reduced_base2d, dpextra_value, data2)

                    rde_2d, _ = ReducedDatumSpecExtra.objects.get_or_create(reduced_datum = rd2d, 
                                                                    reducer = reducer, 
                                                                    show = True,
                                                                    version = v2)

            if not created: # old raw frame, check if there are new extractions -- check the hex from md5 checksum

                dpe = DataProductExtra.objects.filter(data_product = data_product).first()

                try:
                    v1, data1 = self.get_hash(reduced_base1d)
                    v1_list = ReducedDatumSpecExtra.objects.filter(reduced_datum__data_product = data_product, version = v1)

                    if v1_list.count() == 0: 
                        logger.info(f"New hash for old dataproduct {v1} is unique")
                        rd, reducer = self.make_ReducedDatums(data_product, reduced_base1d, dpe.value, data1)
                        logger.info(f"Created Reduced Datum for {reduced_base1d} ")
                        rde, _ = ReducedDatumSpecExtra.objects.get_or_create(reduced_datum = rd, 
                                                                   reducer = reducer, 
                                                                   show = True,
                                                                   version = v1)
                        logger.info(f"Created Reduced Datum Extra for {reduced_base1d} ")
                        
            
                except Exception as e:
                    logger.info(f"{e} Didn't make ReducedDatum for {reduced_base1d}.")
                
                try:
            
                    v2, data2 = self.get_hash(reduced_base2d)
                    v2_list = ReducedDatumSpecExtra.objects.filter(reduced_datum__data_product = data_product, version = v2)

                    if v2_list.count() == 0: 
                        logger.info(f"New hash for old dataproduct {v2} is unique")
                        rd, reducer = self.make_ReducedDatums(data_product, reduced_base2d, dpe.value, data2)
                        logger.info(f"Created Reduced Datums for {reduced_base2d} ")
                        rde, _ = ReducedDatumSpecExtra.objects.get_or_create(reduced_datum = rd, 
                                                                    reducer = reducer, 
                                                                    show = True,
                                                                    version = v2)
                  
                except Exception as e:
                    logger.info(f"{e} Didn't make ReducedDatum for {reduced_base2d}.")
                