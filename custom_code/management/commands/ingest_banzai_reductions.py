from django.core.management.base import BaseCommand
from django.http import FileResponse
from tom_dataproducts.models import ReducedDatum, DataProduct, ObservationRecord
from tom_targets.models import Target, TargetName
from tom_targets.sharing import continuous_share_data
from tom_dataproducts.processors.data_serializers import SpectrumSerializer
from custom_code.models import DataProductExtra, ReducedDatumSpecExtra 
from custom_code.processors.spectroscopy_processor import process_fits_file
from custom_code.hooks import get_metadata
from astropy.time import Time
from io import BytesIO
import requests
import hashlib


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
        
        version = results[0]['version_set'][0].get('md5', False)
        data = requests.get(results[0]["url"]).content
        file =  FileResponse(BytesIO(data),filename=basename+'.fits')
        
        if not version:
            hash = hashlib.md5()
            with open(file, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hash.update(chunk)
            version = hash.hexdigest()
        return version, file
    
    def check_unique(self, hash, rde_list):
        """Returns True/False"""
        rd_hash_list = [rde.version for rde in rde_list]
        if hash not in rd_hash_list:
            return True
        else:
            logger.info(f"Hash {hash} is not unique.")
            return False
            


    def make_ReducedDatums(self, dp, basename, dp_extras):
        token = settings.FACILITIES['LCO']['api_key']
        url = settings.FACILITIES['LCO']['archive_url']
        results = requests.get(url,
                            headers={'Authorization': f'Token {token}'}, 
                            params={'basename_exact': basename, 'include_related_frames': False}).json()["results"]
        
        data = requests.get(results[0]["url"]).content
        file =  FileResponse(BytesIO(data),filename=basename+'.fits')

        # Make ReducedDatum objects
        spectrum, dp_extras, date_obs = process_fits_file(file, dp_extras)

        serialized_spectrum = SpectrumSerializer().serialize(spectrum)

        reduced_datum = ReducedDatum(target=dp.target, data_product=dp, data_type=dp.data_product_type,
                            timestamp=date_obs, value=serialized_spectrum)
       
        continuous_share_data(dp.target, reduced_datum)
        return reduced_datum
    

    def handle(self, *args, **kwargs):

        token = settings.FACILITIES['LCO']['api_key']
        authtoken = {'Authorization': 'Token ' + token}

        # get_metadata will take from window starting 7 days prior to now

        # how to make this query faster?
        raw_frames = get_metadata(
            authtoken=authtoken,
            OBSTYPE='SPECTRUM',
            RLEVEL = '0',        
            include_related_frames = False,
            include_thumbnails = True,
            public = False
        ) 
        # query archive for all new frames, 

        # ingest raw, 1d and 2d and get thumbnails from matched 2ds
     
        # how to know if spectrum was attempted if banzai fails - ingest e00 

    
        e00_no_extractions = {}
        for frame in raw_frames:
            observation_id = frame['observation_id']
            obs_record = ObservationRecord.objects.get(observation_id = observation_id) 

            data_product, created = DataProduct.objects.get_or_create(product_id = frame['id'], 
                                                                      data_product_type = 'spectroscopy', 
                                                                      target = obs_record.target,
                                                                      extra_data = frame['basename'])
            # store a version if the observation record is the same
            reduced_base1d = frame['basename'].replace('e00', 'e91-1d')
            reduced_base2d = frame['basename'].replace('e00', 'e91-2d')
            
            if created: # New raw frame observation, new dp - no reduceddatum currently associated
                dpextra_value = {
                    'telescope':  frame.get('TELID', ''),
                    'instrument': frame.get('INSTRUME', ''),
                    'site':       frame.get('SITEID', ''),
                    'exptime':    frame.get('EXPTIME', ''),
                    'reducer':    'Banzai-Floyds',       # fill in if known -- how to change if reduced manually??
                    'airmass':    frame.get('AIRMASS', ''),
                }
               
                reduced_base1d = frame['basename'].replace('e00', 'e91-1d')
                reduced_base2d = frame['basename'].replace('e00', 'e91-2d')
                
                rde_list = ReducedDatumSpecExtra.objects.filter(target = obs_record.target, data_product = data_product)

                v1, _ = self.get_hash(reduced_base1d)
                if self.check_unique(v1, rde_list):  
                    rd1d = self.make_ReducedDatums(data_product, reduced_base1d, dpextra_value)
                    

                    rde_1d = ReducedDatumSpecExtra(target = obs_record.target, 
                                                                    data_product = data_product, reduced_datum = rd1d, 
                                                                    reducer = 'Banzai-Floyds', 
                                                                    show = True,
                                                                    version = v1)
                    
                    rde_1d.save()

                
                v2, _ = self.get_hash(reduced_base2d)
                if self.check_unique(v2, rde_list):  
                    rd2d = self.make_ReducedDatums(data_product, reduced_base2d, dpextra_value)

                    rde_2d = ReducedDatumSpecExtra(target = obs_record.target, 
                                                                    data_product = data_product, reduced_datum = rd2d, 
                                                                    reducer = 'Banzai-Floyds', 
                                                                    show = True,
                                                                    version = v2)
                    
                    rde_2d.save()

                #dpextra_value['best_rd'] = rd1d.pk # automatically set best version if only one reduction
               
                data_product_extra = DataProductExtra(
                            target = obs_record.target,
                            data_product = data_product,
                            data_type = data_product.data_product_type,
                            key = 'spec_extras',
                            value = dpextra_value,
                            viewed = False
                        )   
                
                data_product_extra.save()


            if not created: # old raw frame, check if there are new extractions -- check the hex from md5 checksum
                # Get existing ReducedDatumSpecExtra objects
                rde_list = ReducedDatumSpecExtra.objects.filter(target = obs_record.target, data_product = data_product)
                
                #current_versions = dpe.value['version_list'] # gives dictionary of form of {rd.pk: hash}

                reduced_base1d = frame['basename'].replace('e00', 'e91-1d')
                reduced_base2d = frame['basename'].replace('e00', 'e91-2d')

                try:
                    v2, _ = self.get_hash(reduced_base2d)
                    if self.check_unique(v2, rde_list):  
                        rd = self.make_ReducedDatums(data_product, reduced_base2d, dpe.value)
                        rde = ReducedDatumSpecExtra(target = obs_record.target, 
                                                                    data_product = data_product, reduced_datum = rd, 
                                                                    reducer = 'Banzai-Floyds', 
                                                                    show = True,
                                                                    version = v2)
                        rde.save()
            
                except:
                    logger.info(f"Didn't make ReducedDatum for {reduced_base2d}.")
                
                try:
                    v1, _ = self.get_hash(reduced_base1d)

                    if self.check_unique(v1, rde_list): # version isn't already in database, make RD
                        rd = self.make_ReducedDatums(data_product, reduced_base1d, dpe.value)
                        rde = ReducedDatumSpecExtra(target = obs_record.target, 
                                                                   data_product = data_product, reduced_datum = rd, 
                                                                   reducer = 'Banzai-Floyds', 
                                                                   show = True,
                                                                   version = v1)
                        rde.save()
                
                except:
                    logger.info(f"Didn't make ReducedDatum for {reduced_base1d}.")

                    date_obs = frame('DATE_OBS')
                    if isinstance(date_obs, str) and date_obs.endswith('Z'):
                        date_obs = date_obs[:-1]
                    obs_time = Time(date_obs, format='isot', scale='utc')
                    time_since_exp = Time.now() - obs_time
                    e00_no_extractions[frame['basename']] = time_since_exp 



                   
                
                
                
                # Work flow: run a chron to ingest new banzai reductions from the archive (how to filter for this??? perhaps just a time frame?). This script then makes a dataproduct and reduceddatum object for the spectrum, with approval = 0 as default. 
                # Target pages need to be updated to query reduceddatum table with approval flag '0' or '1'
                # Floyds Inbox needs to query for approval flag '0' with buttons that link to a script changing the reduceddatum approval flag to 1 or -1. Changing the flag to -1 should call the modify_sequence function in scheduling.py to re-request the sequence immediately (mimiccing markbad behaviour).

                # Run a one-time script to make all previous spectra approved, with flag approval = 1 in ReducedDatum table

                # if obs_record marked as completed but no dp -- ingest e00 -- some time limit between obs_record update and banzai reduction - if there isn't a dp  for it - have a 'this long since obs taken' and e00 for all spectra until reduced by banzai or failed (reduction should take less than an hour)
                # make this the 2D frame, can pass file and thumbnail?

                # Currently a dataproduct extra - one to one with dataproduct, not reduceddatum
                # to search through dictionary: DataProductExtra.objects.filter(value__reduceddatum_id = rd.pk)

                #reduced_data, rdextra_value = run_custom_data_processor(data_product, extras, rdextra_value)

                # ask tomtoolkit team if there is a way to encode reducer and version into reduceddatum (or just json metadata field that we can populate)


                # rd = ReducedDatum.objects.filter(data_product = data_product, target = obs_record.target)
                
                # rdextra_value['version_list']= {rd.pk : frame.get('version_set'[0]).get('md5')},
                # rdextra_value['best_rd'] = rd.pk # automatically set best version if only one reduction

                # md5 checksum - checks if two files are the same and returns a hex for each file - can be in buffer, don't need to download it to harddisk in a directory -- one of the archive parameters we can query on is the hex code, so if the file exists we don't have to download again. make the hex code a column in the table
