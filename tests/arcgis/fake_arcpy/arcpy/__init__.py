"""A stand-in for arcpy, enough to run geolens.pyt without ArcGIS Pro.

It records what a tool asks ArcGIS to do -- progress, messages, copies,
renderers, pop-ups -- so the tests can check the toolbox's own logic and its
calls into real geolens runs. It says nothing about how ArcGIS Pro itself
behaves; that needs Pro.
"""
from __future__ import annotations

import os
import shutil
import types

LOG: list = []


def reset():
    LOG.clear()
    env.isCancelled = False
    env.autoCancelling = True
    mp.MAP.layers.clear()
    mp.MAP.moves.clear()


class ExecuteError(Exception):
    pass


class _Filter:
    def __init__(self):
        self.type = None
        self.list = []


class Parameter:
    def __init__(self, displayName="", name="", datatype="",
                 parameterType="Required", direction="Input",
                 multiValue=False, category=None, enabled=True):
        self.displayName = displayName
        self.name = name
        self.datatype = datatype
        self.parameterType = parameterType
        self.direction = direction
        self.multiValue = multiValue
        self.category = category
        self.enabled = enabled
        self.value = None
        self.filter = _Filter()
        self.message = None
        self.messageType = None

    @property
    def valueAsText(self):
        return None if self.value is None else str(self.value)

    def setErrorMessage(self, m):
        self.message, self.messageType = m, "error"

    def setWarningMessage(self, m):
        self.message, self.messageType = m, "warning"

    def clearMessage(self):
        self.message, self.messageType = None, None

    def hasError(self):
        return self.messageType == "error"


class _Env:
    def __init__(self):
        self.autoCancelling = True
        self.isCancelled = False
        self.overwriteOutput = True
        self.scratchFolder = os.path.join(
            os.environ.get("TMPDIR", "/tmp"), "fake_arcpy_scratch")


env = _Env()


def SetProgressor(*a):
    LOG.append(("progressor", a))


def SetProgressorLabel(label):
    LOG.append(("label", label))


def SetProgressorPosition(pos):
    LOG.append(("position", pos))


def ResetProgressor():
    LOG.append(("reset",))


def AddMessage(m):
    LOG.append(("message", str(m)))


def AddWarning(m):
    LOG.append(("warning", str(m)))


def AddError(m):
    LOG.append(("error", str(m)))


class _Desc:
    def __init__(self, path, fidset=""):
        self.catalogPath = str(path)
        self.FIDSet = fidset
        self.OIDFieldName = "OBJECTID"


class SelectedLayer:
    """A feature layer with a selection: Describe gives a FIDSet."""

    def __init__(self, path):
        self.path = str(path)

    def __str__(self):
        return self.path


def Describe(value):
    if isinstance(value, SelectedLayer):
        return _Desc(value.path, "1;2")
    return _Desc(value)


def _split_fc(path):
    """(file, layer) for a GeoPackage layer, or for a feature class in a
    stand-in file geodatabase (a folder holding <name>.gpkg files)."""
    low = str(path).lower()
    if ".gdb" in low:
        i = low.index(".gdb") + 4
        name = str(path)[i:].lstrip("\\/")
        return os.path.join(str(path)[:i], name + ".gpkg"), name
    i = low.index(".gpkg") + 5
    return str(path)[:i], str(path)[i:].lstrip("\\/").split(".", 1)[-1]


def Exists(path):
    p = str(path)
    if ".gdb" in p.lower() and not p.lower().endswith(".gdb"):
        return os.path.exists(_split_fc(p)[0])
    return os.path.exists(p)


class management:
    @staticmethod
    def CopyRaster(src, dst):
        LOG.append(("CopyRaster", str(src), str(dst)))
        shutil.copyfile(str(src), str(dst))

    @staticmethod
    def CreateSQLiteDatabase(path, spatial_type):
        LOG.append(("CreateSQLiteDatabase", str(path), spatial_type))

    @staticmethod
    def CopyFeatures(src, dst):
        import geopandas as gpd

        LOG.append(("CopyFeatures", str(src), str(dst)))
        gpkg, layer = _split_fc(dst)
        path = src.path if isinstance(src, SelectedLayer) else str(src)
        if ".gpkg" in path.lower() and not path.lower().endswith(".gpkg"):
            g, lyr = _split_fc(path)
            gdf = gpd.read_file(g, layer=lyr)
        else:
            gdf = gpd.read_file(path)
        gdf.to_file(gpkg, layer=layer, driver="GPKG")

    @staticmethod
    def Delete(path):
        p = str(path)
        if ".gdb" in p.lower() and not p.lower().endswith(".gdb"):
            p = _split_fc(p)[0]
        os.remove(p)

    @staticmethod
    def CreateFileGDB(folder, name):
        LOG.append(("CreateFileGDB", str(folder), name))
        os.makedirs(os.path.join(str(folder), name), exist_ok=True)

    @staticmethod
    def EnableAttachments(fc):
        LOG.append(("EnableAttachments", str(fc)))
        # ArcGIS makes <fc>__ATTACH; the stand-in gdb gets a file for it.
        open(_split_fc(f"{fc}__ATTACH")[0], "a").close()

    @staticmethod
    def AddAttachments(fc, join, match, match_join, path_field, *rest):
        rows = _read(match)
        n = int(rows[path_field].notna().sum())
        LOG.append(("AddAttachments", str(fc), path_field, n))


class _Field:
    def __init__(self, name, type_="String"):
        self.name = name
        self.type = type_


def _read(source):
    import geopandas as gpd

    if ".gdb" in str(source).lower() or ".gpkg" in str(source).lower():
        g, lyr = _split_fc(source)
        return gpd.read_file(g, layer=lyr)
    return gpd.read_file(str(source))


def ListFields(layer):
    src = layer.dataSource if hasattr(layer, "dataSource") else str(layer)
    df = _read(src)
    return [_Field(c) for c in df.columns if c != "geometry"]


class _UpdateCursor:
    def __init__(self, fc, fields):
        self.fc, self.fields = str(fc), list(fields)
        self.df = _read(self.fc)
        self.i = -1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        gpkg, layer = _split_fc(self.fc)
        self.df.to_file(gpkg, layer=layer, driver="GPKG")

    def __iter__(self):
        for self.i in range(len(self.df)):
            yield [self.df.iloc[self.i][f] for f in self.fields]

    def updateRow(self, row):
        for f, val in zip(self.fields, row):
            self.df.at[self.df.index[self.i], f] = val


class da:
    UpdateCursor = _UpdateCursor


# -- symbology -------------------------------------------------------------------

class _Symbol:
    def __init__(self):
        self.color = None
        self.size = None


class _Item:
    def __init__(self, value):
        self.values = [[value]]
        self.symbol = _Symbol()
        self.label = value


class _UniqueValueRenderer:
    type = "UniqueValueRenderer"

    def __init__(self, layer):
        self._layer = layer
        self._fields = []
        self.groups = []

    @property
    def fields(self):
        return self._fields

    @fields.setter
    def fields(self, names):
        self._fields = list(names)
        df = _read(self._layer.dataSource)
        vals = sorted({str(v) for v in df[names[0]].dropna()})
        self.groups = [types.SimpleNamespace(items=[_Item(v) for v in vals])]


class _SimpleRenderer:
    type = "SimpleRenderer"

    def __init__(self):
        self.symbol = _Symbol()


class _Symbology:
    def __init__(self, layer):
        self._layer = layer
        self.renderer = _SimpleRenderer()

    def updateRenderer(self, kind):
        self.renderer = (_UniqueValueRenderer(self._layer)
                         if kind == "UniqueValueRenderer" else _SimpleRenderer())


class Layer:
    """A layer Pro would have added to the map for a derived output."""

    def __init__(self, source):
        self.dataSource = str(source)
        self.name = os.path.basename(str(source))
        self._symbology = _Symbology(self)
        self._cim = types.SimpleNamespace(popupInfo=None)
        self.applied_symbology = None
        self.visible = True

    def supports(self, what):
        return what in ("DATASOURCE", "NAME", "SYMBOLOGY")

    @property
    def symbology(self):
        return self._symbology

    @symbology.setter
    def symbology(self, sym):
        self.applied_symbology = sym
        self._symbology = sym

    def getDefinition(self, version):
        return self._cim

    def setDefinition(self, cim):
        self._cim = cim


class _Map:
    def __init__(self):
        self.layers = []
        self.moves = []

    def listLayers(self):
        return list(self.layers)

    def addDataFromPath(self, path):
        LOG.append(("addDataFromPath", str(path)))
        lyr = Layer(path)
        self.layers.append(lyr)
        return lyr

    def moveLayer(self, reference, layer, where="BEFORE"):
        """The layer list here is bottom to top (the order layers were
        added); Pro's is top to bottom, so AFTER (below) is before here."""
        self.moves.append((reference.name, layer.name, where))
        self.layers.remove(layer)
        i = self.layers.index(reference)
        self.layers.insert(i if where == "AFTER" else i + 1, layer)


class mp:
    MAP = _Map()

    class ArcGISProject:
        def __init__(self, which):
            self.activeMap = mp.MAP


class cim:
    @staticmethod
    def CreateCIMObjectFromClassName(name, version):
        base = {"CIMImageMediaInfo": dict(sourceURL=None, linkURL=None,
                                          title=None, caption=None),
                "CIMTableMediaInfo": dict(fields=None, title=None),
                "CIMPopupInfo": dict(title=None, mediaInfos=None)}
        if name not in base:
            raise ValueError(name)
        return types.SimpleNamespace(_class=name, **base[name])
