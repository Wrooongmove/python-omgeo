from base import GeocodeService
import logging
from omgeo.places import Candidate
from omgeo.preprocessors import CancelIfPOBox
from omgeo.postprocessors import (AttrFilter, AttrRename, AttrSorter, UseHighScoreIfAtLeast,
                                  GroupBy, ScoreSorter)

logger = logging.getLogger(__name__)


class EsriWGS(GeocodeService):
    """
    Class to geocode using the `ESRI World Geocoding service
    <http://geocode.arcgis.com/arcgis/geocoding.html>`_.

    This uses two endpoints -- one for single-line addresses,
    and one for multi-part addresses.

    An optional (key) parameter can be passed to the PlaceQuery
    which will be passed as a magicKey to the find endpoint if
    using a single line address/text search. This allows omgeo
    to be used with the `Esri suggest endpoint
    <https://developers.arcgis.com/rest/geocode/api-reference/geocoding-suggest.htm>`_.

    Note: Based on tests using the magicKey parameter, it is
    recommended that a viewbox not be used with in conjuction
    with the magicKey. Additionally, address/search text passed
    via the query may be ignored when using a magicKey.
    """

    LOCATOR_MAP = {
        'PointAddress': 'rooftop',
        'StreetAddress': 'interpolation',
        'PostalExt': 'postal_specific',  # accept ZIP+4
        'Postal': 'postal'
    }

    DEFAULT_PREPROCESSORS = [CancelIfPOBox()]

    DEFAULT_POSTPROCESSORS = [
        AttrFilter(['PointAddress',
                    'StreetAddress',
                    # 'PostalExt',
                    # 'Postal'
                    ],
                   'locator_type'),
        # AttrExclude(['USA_Postal'], 'locator'), #accept postal from everywhere but US (need PostalExt)
        AttrSorter(['PointAddress',
                    'StreetAddress',
                    # 'PostalExt',
                    # 'Postal'
                    ],
                   'locator_type'),
        AttrRename('locator', LOCATOR_MAP),  # after filter to avoid searching things we toss out
        UseHighScoreIfAtLeast(99.8),
        ScoreSorter(),
        GroupBy('match_addr'),
        GroupBy(('x', 'y')),
    ]

    _endpoint = 'http://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer'

    def _geocode(self, pq):
        """
        :arg PlaceQuery pq: PlaceQuery object to use for geocoding
        :returns: list of location Candidates
        """
        #: List of desired output fields
        #: See `ESRI docs <http://geocode.arcgis.com/arcgis/geocoding.html#output>_` for details
        outFields = ('Loc_name',
                     # 'Shape',
                     'Score',
                     'Match_addr',  # based on address standards for the country
                     # 'Address', # returned by default
                     # 'Country' # 3-digit ISO 3166-1 code for a country. Example: Canada = "CAN"
                     # 'Admin',
                     # 'DepAdmin',
                     # 'SubAdmin',
                     # 'Locality',
                     # 'Postal',
                     # 'PostalExt',
                     'Addr_type',
                     # 'Type',
                     # 'Rank',
                     'AddNum',
                     'StPreDir',
                     'StPreType',
                     'StName',
                     'StType',
                     'StDir',
                     # 'Side',
                     # 'AddNumFrom',
                     # 'AddNumTo',
                     # 'AddBldg',
                     'City',
                     'Subregion',
                     'Region',
                     'Postal',
                     'Country',
                     # 'Ymax',
                     # 'Ymin',
                     # 'Xmin',
                     # 'Xmax',
                     # 'X',
                     # 'Y',
                     'DisplayX',
                     'DisplayY',
                     # 'LangCode',
                     # 'Status',
                     )
        outFields = ','.join(outFields)
        query = dict(f='json',  # default HTML. Other options are JSON and KMZ.
                     outFields=outFields,
                     # outSR=WKID, defaults to 4326
                     maxLocations=20,  # default 1; max is 20
                     )

        # Postal-code only searches work in the single-line but not multipart geocoder
        # Remember that with the default postprocessors, postcode-level results will be eliminated
        if pq.query == pq.address == '' and pq.postal != '':
            pq.query = pq.postal

        if pq.query == '':  # multipart
            query = dict(query,
                         Address=pq.address,  # commonly represents the house number and street name of a complete address
                         Neighborhood=pq.neighborhood,
                         City=pq.city,
                         Subregion=pq.subregion,
                         Region=pq.state,
                         Postal=pq.postal,
                         # PostalExt=
                         CountryCode=pq.country,  # full country name or ISO 3166-1 2- or 3-digit country code
                         )
        else:  # single-line
            magic_key = pq.key if hasattr(pq, 'key') else ''
            query = dict(query,
                         singleLine=pq.query,  # This can be a street address, place name, postal code, or POI.
                         sourceCountry=pq.country,  # full country name or ISO 3166-1 2- or 3-digit country code
                         )
            if magic_key:
                query['magicKey'] = magic_key  # This is a lookup key returned from the suggest endpoint.

        if pq.bounded and pq.viewbox is not None:
            query = dict(query, searchExtent=pq.viewbox.to_esri_wgs_json())

        endpoint = self._endpoint + '/findAddressCandidates'
        response_obj = self._get_json_obj(endpoint, query)
        returned_candidates = []  # this will be the list returned
        try:
            locations = response_obj['candidates']
            for location in locations:
                c = Candidate()
                attributes = location['attributes']
                c.match_addr = attributes['Match_addr']
                c.locator = attributes['Loc_name']
                c.locator_type = attributes['Addr_type']
                c.score = attributes['Score']
                c.x = attributes['DisplayX']  # represents the actual location of the address.
                c.y = attributes['DisplayY']
                c.wkid = response_obj['spatialReference']['wkid']
                c.geoservice = self.__class__.__name__

                # Optional address component fields.
                for in_key, out_key in [('City', 'match_city'), ('Subregion', 'match_subregion'),
                                        ('Region', 'match_region'), ('Postal', 'match_postal'),
                                        ('Country', 'match_country')]:
                    setattr(c, out_key, attributes.get(in_key, ''))
                setattr(c, 'match_streetaddr', self._street_addr_from_response(attributes))
                returned_candidates.append(c)
        except KeyError:
            pass
        return returned_candidates

    def _street_addr_from_response(self, attributes):
        """Construct a street address (no city, region, etc.) from a geocoder response.

        :param attributes: A dict of address attributes as returned by the Esri geocoder.
        """
        # The exact ordering of the address component fields that should be
        # used to reconstruct the full street address is not specified in the
        # Esri documentation, but the examples imply that it is this.
        ordered_fields = ['AddNum', 'StPreDir', 'StPreType', 'StName', 'StType', 'StDir']
        result = []
        for field in ordered_fields:
            result.append(attributes.get(field, ''))
        if any(result):
            return ' '.join([s for s in result if s])  # Filter out empty strings.
        else:
            return ''

    def __init__(self, preprocessors=None, postprocessors=None, settings=None):
        preprocessors = EsriWGS.DEFAULT_PREPROCESSORS if preprocessors is None else preprocessors
        postprocessors = EsriWGS.DEFAULT_POSTPROCESSORS if postprocessors is None else postprocessors
        GeocodeService.__init__(self, preprocessors, postprocessors, settings)


class EsriWGSSSL(EsriWGS):
    """
    Class to geocode using the `ESRI World Geocoding service over SSL
    <https://geocode.arcgis.com/arcgis/geocoding.html>`_.
    """
    _endpoint = 'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer'
