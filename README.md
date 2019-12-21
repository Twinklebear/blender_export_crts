# Blender CRTS Exporter Plugin

This is a plgin to export files in a glb-like format used by
[ChameleonRT](https://github.com/Twinklebear/ChameleonRT).
The main differences are that CRTS uses the
Disney Principled BSDF material mode, supports area lights,
and does not support node hierarchies. The mesh/instance model
also differs slightly, since Blender only supports a primitive
primitive per-mesh vs. glTF which can store multiple primitives
per-mesh. Since this plugin is just for a final export of Blender to
ChameleonRT it does the former. Material IDs are specified at
the instance level instead of the primitive level, to allow
easy instancing of primitives with different materials.

For now an example importer can be found in
[ChameleonRT](https://github.com/Twinklebear/ChameleonRT/blob/crts/util/scene.cpp#L363-L475).

