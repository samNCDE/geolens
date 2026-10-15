def classFactory(iface):                                     # noqa: N802
    from .plugin import GeolensPlugin
    return GeolensPlugin(iface)
