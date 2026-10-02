import math
import re

MATCH_RADIUS_ARCSEC = 1.0

TNS_PREFIX_RE = re.compile(r'^(SN|AT)(?=\d)', re.IGNORECASE)

def ra_ranges(ra, dec, radius):

    if abs(dec) + radius >= 90:
        return None
    dra = math.degrees(math.asin(math.sin(math.radians(radius)) / math.cos(math.radians(dec))))
    dra = dra * 1.001 + 1e-9
    ra %= 360
    return [(ra - dra + shift, ra + dra + shift) for shift in (0, 360, -360)]

def angular_separation(ra1, dec1, ra2, dec2):
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))
