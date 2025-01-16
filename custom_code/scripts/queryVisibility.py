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


#------------- SETTINGS -------------#
APT_APPLICATION_PATH = '/Applications/APT2024.7/bin/apt'

#------------- CLASSES -------------#
class UpdateTargetCoords(object):
    """
    Perform the functionality in `update_target_coords.py`
    """

    def __init__(self, target_name, target_ra, target_dec):

        self.target_name = str(target_name)
        self.target_ra = str(target_ra).replace(':', ' ')
        self.target_dec = str(target_dec).replace(':', ' ')
        self.tree = None

        print("Updating template with target coordinates...")
        self.__update_target_coords()

    def __update_target_coords(self):

        import argparse
        import xml.etree.ElementTree as ET
     
        tree = ET.parse('HST_FOR_template.apt')
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
        self.tree.write('HST_FOR_template.apt') # legacy from when we were loading and reloading

class AccessAPT(object):
    """
    Connects to HST APT and requests the visibility."
    """
    def __init__(self, target_name, target_ra, target_dec):    
        self.target_name = str(target_name)
        self.target_ra = str(target_ra).replace(':', ' ')
        self.target_dec = str(target_dec).replace(':', ' ')
        self.tree = None

        print("Accessing APT...")
        self.__access_apt()

    def __access_apt(self):
        global APT_APPLICATION_PATH

        command = [
            APT_APPLICATION_PATH,
            "--forcesaves",
            "--runall",
            "--nogui",
            "HST_FOR_template.apt"
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

    def __init__(self, target_name):
        self.target_name = str(target_name)

        self.__write_visibility()

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

        tree = ET.parse('HST_FOR_template.apt')
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


def clean_up_dir():
    """cleans up unnecessary files in directory created by APT"""
    import os, shutil, glob
    for _fpath in glob.glob("./*.aptbackup") + glob.glob("./*.vot"):
        os.remove(_fpath)
    shutil.rmtree('./OP-cache/')


#------------- FUNCTIONS -------------#
def main():
    print("Performing unit test with SN 2024abfo...")
    # ./check_hst.sh SN2024abfo 59.3567 -46.1854
    UTCStep1Obj = UpdateTargetCoords("SN2024abfo", "59.3567", "-46.1854")
    AAPTStep2Obj = AccessAPT(UTCStep1Obj.target_name, UTCStep1Obj.target_ra, UTCStep1Obj.target_dec)
    WVStep2Obj = WriteVisibility(UTCStep1Obj.target_name)
    print("Testing clean up...")
    clean_up_dir()


if __name__ == '__main__':
    main()