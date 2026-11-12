"""Identifica insumos y comidas que se comercian sin calidad de equipo."""
import re
from functools import lru_cache


MATERIALES, ARTEFACTOS = 'Materiales', 'Artefactos'
_INSUMOS = frozenset('''
    WOOD PLANKS ORE METALBAR FIBER CLOTH HIDE LEATHER ROCK STONEBLOCK
    RUNE SOUL RELIC SHARD FISH FISHCHOPS FISHSAUCE FARM ALCHEMY MEAT
    MILK EGG BUTTER CABBAGE TEASEL CORN AGARIC BREAD FLOUR YARROW BEAN
    FOXGLOVE MULLEIN POTATO PUMPKIN COMFREY WHEAT BURDOCK SEAWEED TURNIP
    CARROT FACTION
'''.split())
_TIER = re.compile(r'^T[1-8]_')
_NIVEL = re.compile(r'@([1-4])$')


def cuerpo_objeto(item_id):
    return _TIER.sub('', (item_id or '').split('@', 1)[0])


@lru_cache(maxsize=16384)
def tipo_insumo(item_id):
    cuerpo = cuerpo_objeto(item_id)
    if (cuerpo.startswith(('ARTEFACT_', 'QUESTITEM_TOKEN_'))
            or cuerpo.startswith('CAPEITEM_') and cuerpo.endswith('_BP')):
        return ARTEFACTOS
    return MATERIALES if cuerpo.split('_', 1)[0] in _INSUMOS else None


def nivel_catalogo(item_id):
    nivel = _NIVEL.search(item_id)
    return int(nivel[1]) if nivel else 0


def es_comida(item_id):
    return cuerpo_objeto(item_id).startswith('MEAL_')


def sin_calidad(item_id):
    return bool(tipo_insumo(item_id) or es_comida(item_id))


def calidad_mercado(item_id, elegida):
    # La API y la base de precios representan «sin calidad» con 1.
    return 1 if sin_calidad(item_id) else elegida
