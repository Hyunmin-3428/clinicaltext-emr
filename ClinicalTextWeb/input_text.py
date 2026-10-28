"""Accept compatible plain-text typography at the web ingress boundary."""
import unicodedata
from ClinicalCDSGateway.gateway.common import plain_text

def normalize_text(value):
    if not isinstance(value,str):
        plain_text(value)
    normalized=unicodedata.normalize('NFKC',value.replace('\r\n','\n').replace('\r','\n'))
    plain_text(normalized)
    return normalized
