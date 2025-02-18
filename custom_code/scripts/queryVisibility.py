"""
====================================

* **Filename**:         queryVisibility.py 
* **Author**:              Joseph Farah 
* **Description**:       Tools for grabbing and converting HST vis info to 
                            machine and human-readable formats.     

====================================

**Notes**
*  This is based on Azalee's HST visibility code.
"""
 
#------------- IMPORTS -------------#
import subprocess
import os


#------------- SETTINGS -------------#
APT_APPLICATION_PATH = '/Applications/APT2024.7/bin/apt'
HST_TEMPLATE_DIR = '/Users/josephfarah/Documents/phd/snex2/snex2_baremetal/snex2/custom_code/scripts/'

OBJ_TEMPLATE_NAME = 'HST_FOR_template.apt'

#------------- CLASSES -------------#
class UpdateTargetCoords(object):
    """
    Perform the functionality in `update_target_coords.py`
    """

    def __init__(self, target_name, target_ra, target_dec, template_fname=OBJ_TEMPLATE_NAME):

        self.target_name = str(target_name)
        self.target_ra = str(target_ra).replace(':', ' ')
        self.target_dec = str(target_dec).replace(':', ' ')
        self.tree = None
        self.template_fname = template_fname

        print("Updating template with target coordinates...")
        self.__update_target_coords()

    def __update_target_coords(self):

        import argparse
        import xml.etree.ElementTree as ET
     
        tree = ET.parse(HST_TEMPLATE_DIR + OBJ_TEMPLATE_NAME)
        root = tree.getroot()
        for child in root:
            if child.tag == 'Targets':
                for i in child:
                    if i.tag == 'FixedTarget':
                        i.set('ProvisionalCoordinates', f'{self.target_ra} {self.target_dec}')
                        print(f'Setting ProvisionalCoordinates {i.get("ProvisionalCoordinates")}')
                        #i.set()
                        #break
                    for j in i:
                        if j.tag == 'EquatorialPosition':
                            j.set('Value', f'{self.target_ra} {self.target_dec}')
                            print(f'Setting EquatorialPosition {j.get("Value")}')
            if child.tag == 'Visits':
                for k in child:
                    if k.tag == 'Visit':
                        for m in k:
                            if m.tag == 'ToolData':
                                for n in m:
                                    for p in n:
                                        if p.tag == 'VpVisitData':
                                            for q in p:
                                                if q.tag == 'AncillaryData':
                                                    for r in q:
                                                        r.set('Coordinates', f'RA: {self.target_ra},  DEC: {self.target_dec}')
                                                        print(f'Setting Coordinates {r.get("Coordinates")}')
                                                if q.tag =='StVisitSchedulingWindows':
                                                    print(f'Setting UpToDate to false from {q.get("UpToDate")}')    
                                                    q.set('UpToDate', 'false')
        self.tree = tree
        self.tree.write(HST_TEMPLATE_DIR + self.template_fname) # legacy from when we were loading and reloading

class AccessAPT(object):
    """
    Connects to HST APT and requests the visibility."
    """
    def __init__(self, target_name, target_ra, target_dec, template_fname=OBJ_TEMPLATE_NAME):    
        self.target_name = str(target_name)
        self.target_ra = str(target_ra).replace(':', ' ')
        self.target_dec = str(target_dec).replace(':', ' ')
        self.tree = None
        self.template_fname = template_fname

        print("Accessing APT...")
        self.__access_apt()

    def __access_apt(self):
        global APT_APPLICATION_PATH

        command = [
            APT_APPLICATION_PATH,
            "--forcesaves",
            "--runall",
            "--nogui",
            HST_TEMPLATE_DIR+self.template_fname
        ]

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        ## log output ##
        for line in iter(process.stdout.readline, b''):
            print(line.decode().strip())

        exit_code = process.wait()
        print(f"HST APT exited with code: {exit_code}.")
                

class WriteVisibility(object):
    """
    Retrieve the visibility of the object.
    """

    def __init__(self, target_name, template_fname=OBJ_TEMPLATE_NAME):
        self.target_name = str(target_name)

        self.template_fname = template_fname
        self.output = self.__write_visibility()

    def __write_visibility(self):
        import datetime
        import sys
        import xml.etree.ElementTree as ET
        import argparse
        import datetime
        from astropy.time import Time

        def convert_scheduling_time_to_human_time(time_ms):
            time_s = time_ms/1000
            return datetime.datetime.fromtimestamp(time_s, datetime.timezone.utc)

        tree = ET.parse(HST_TEMPLATE_DIR+self.template_fname)
        root = tree.getroot()

        for child in root:
            if child.tag == 'Visits':
                for i in child:
                    for j in i:
                        if j.tag == 'ToolData':
                            for k in j:
                                for l in k:
                                    #print(l.tag)
                                    if l.tag == 'VpVisitData':
                                        for m in l:
                                            if m.tag == 'StVisitSchedulingWindows':
                                                visit_string = m.items()[1][1]
                                                break
        split_visit_string = visit_string.split()
        start_vis = split_visit_string[1]
        if float(start_vis) == 0.0:  #end visibility window flag
            visit_start = split_visit_string[2::4]
            visit_end = split_visit_string[4::4]
        elif float(start_vis) == 1.0: #start visibility window flag
            visit_start = split_visit_string[::4]
            visit_end = split_visit_string[2::4]

        today = Time(datetime.datetime.today())
        vis_windows_string = []
        VISIBLE_NOW = False
        for start, end in zip(visit_start, visit_end):
            visit_start_timeobj = Time(convert_scheduling_time_to_human_time(int(start)))
            visit_start_timeobj.format='iso'
            visit_end_timeobj = Time(convert_scheduling_time_to_human_time(int(end)))
            visit_end_timeobj.format='iso'
            visit_start_timeobj.out_subfmt='date'
            visit_end_timeobj.out_subfmt='date'
            if (today>= visit_start_timeobj) & (today <= visit_end_timeobj):
                visible_now=True
                print('Visible Now!!!')
                VISIBLE_NOW = True
            vis_windows_string.append((f'{(visit_start_timeobj-today).value:.0f}-{(visit_end_timeobj-today).value:.0f}d from today; {visit_start_timeobj.iso}-{visit_end_timeobj.iso}'))

        print("Recorded, displaying for debugging purposes.")
        print(VISIBLE_NOW)
        for vis in vis_windows_string:
            print(vis)

        class ReturnObject(object):

            def __init__(self, vis_list, vis_string, vstart, vend, visnow):
                self.vis_list = vis_list
                self.vis_string = vis_string
                self.vstart = vstart
                self.vend = vend
                self.visible_now = visnow

        print(visit_start, visit_end)
        return ReturnObject(vis_windows_string, '\n'.join(vis_windows_string), visit_start, visit_end, VISIBLE_NOW)



def clean_up_dir(extra_files = []):
    """cleans up unnecessary files in directory created by APT"""
    import os, shutil, glob
    for _fpath in glob.glob(HST_TEMPLATE_DIR+"*.aptbackup") + glob.glob(HST_TEMPLATE_DIR+"*.vot"):
        os.remove(_fpath)
    for _file in extra_files:
        try:
            os.remove(_file)
        except FileNotFoundError:
            print("Couldn't find file, moving on.")
    # shutil.rmtree(HST_TEMPLATE_DIR+'OP-cache/')



# def check_db_for_coords(target_name):
#     """ given a target name checks the database for coordinates """


def ra_dec_to_decimal(ra_str, dec_str):

    if ':' in ra_str or ':' in dec_str:
        pass
    else: 
        return ra_str, dec_str

    def sexagesimal_to_decimal(coord, is_ra=False):
        parts = list(map(float, coord.split(':')))
        sign = -1 if coord.startswith('-') else 1
        decimal = abs(parts[0]) + parts[1] / 60 + parts[2] / 3600
        if is_ra:
            decimal *= 15  # Convert hours to degrees for RA
        return sign * decimal if not is_ra else decimal

    ra_decimal = sexagesimal_to_decimal(ra_str, is_ra=True)
    dec_decimal = sexagesimal_to_decimal(dec_str, is_ra=False)
    
    return ra_decimal, dec_decimal



def decimal_to_ra_dec(ra, dec):
    def decimal_to_sexagesimal(value, is_ra=False):
        value = float(value)
        sign = "-" if value < 0 else ""
        value = abs(value)
        degrees = int(value)
        minutes = int((value - degrees) * 60)
        seconds = (value - degrees - minutes / 60) * 3600
        
        if is_ra:
            return f"{int(degrees/15):02}:{minutes:02}:{seconds:06.3f}"
        else:
            return f"{sign}{degrees:02}:{minutes:02}:{seconds:06.3f}"
    
    if ':' in str(ra) or ':' in str(dec):
        return ra, dec  # Already in sexagesimal
    
    ra_sexagesimal = decimal_to_sexagesimal(ra, is_ra=True)
    dec_sexagesimal = decimal_to_sexagesimal(dec, is_ra=False)
    
    return ra_sexagesimal, dec_sexagesimal



def dummy(q):
    print("dummy: ", q)
    return q



def get_visibility(query_object):

    with open(HST_TEMPLATE_DIR+".queue", "w") as f:
        f.write("1")

    temp_OBJ_TEMPLATE_NAME = query_object.name + "_" + OBJ_TEMPLATE_NAME
    os.system(f"cp {HST_TEMPLATE_DIR+'HST_FOR_template.apt'} {HST_TEMPLATE_DIR+temp_OBJ_TEMPLATE_NAME}")
    UTCStep1Obj = UpdateTargetCoords(query_object.name, query_object.ra, query_object.dec)
    AAPTStep2Obj = AccessAPT(UTCStep1Obj.target_name, UTCStep1Obj.target_ra, UTCStep1Obj.target_dec)
    WVStep2Obj = WriteVisibility(UTCStep1Obj.target_name)
    clean_up_dir(extra_files = [HST_TEMPLATE_DIR+temp_OBJ_TEMPLATE_NAME])

    with open(HST_TEMPLATE_DIR+".queue", "w") as f:
        f.write("0")


    return WVStep2Obj



#------------- FUNCTIONS -------------#
def main():
    print("Performing unit test...")
    import sys
    # ./check_hst.sh SN2024abfo 59.3567 -46.1854
    UTCStep1Obj = UpdateTargetCoords(sys.argv[1], sys.argv[2], sys.argv[3])
    AAPTStep2Obj = AccessAPT(UTCStep1Obj.target_name, UTCStep1Obj.target_ra, UTCStep1Obj.target_dec)
    WVStep2Obj = WriteVisibility(UTCStep1Obj.target_name)
    print("Working on ", sys.argv[1])

    import dill
    with open(os.path.join(os.path.dirname(__file__), "output.hst"), "wb") as f:
        dill.dump(WVStep2Obj, f)
    print(f"Wrote output file at output.hst for {sys.argv[1]}.")

    print("Testing clean up...")
    clean_up_dir()


if __name__ == '__main__':
    main()