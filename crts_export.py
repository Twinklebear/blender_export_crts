import bpy
import mathutils
import math
import struct
import json
from bpy_extras.io_utils import ExportHelper

bl_info = {
    "name": "crts_export",
    "author": "Will Usher",
    "blender": (2, 80, 0),
    "version": (1, 0, 0),
    "location": "File > Import-Export",
    "description": "Export the scene to a ChameleonRT scene",
    "category": "Import-Export",
    "wiki_url": "online"
}

def compute_mesh_buffer_sizes(mesh):
    # TODO: Also normals? It looks like there's some separate calc_normals_split method
    # but I'm not sure how that'll effect the indexing
    return (len(mesh.vertices) * 3 * 4, len(mesh.loop_triangles) * 3 * 4)

def write_mesh_vertex_buffer(mesh, output):
    for v in mesh.vertices:
        output.extend(struct.pack("<fff", v.co[0], v.co[1], v.co[2]))
        
def write_mesh_index_buffer(mesh, output):
    for t in mesh.loop_triangles:
        output.extend(struct.pack("<III", t.vertices[0], t.vertices[1], t.vertices[2]))

def export_crts(operator, context, filepath=""):
    header = {
        "meshes": [],
        "objects": [],
        "buffer_views": [],
        "materials": []
    }

    scene = context.scene
    mesh_indices = {}
    byte_offset = 0
    for mesh in bpy.data.meshes:
        mesh.calc_loop_triangles()
        verts_size, indices_size = compute_mesh_buffer_sizes(mesh)

        positions_view = len(header["buffer_views"])
        header["buffer_views"].append({
            "byte_offset": byte_offset,
            "byte_length": verts_size,
            "type": "VEC3_F32"
        })
        byte_offset += verts_size

        indices_view = positions_view + 1
        header["buffer_views"].append({
            "byte_offset": byte_offset,
            "byte_length": indices_size,
            "type": "VEC3_U32"
        })
        byte_offset += indices_size

        mesh_indices[mesh.name] = len(header["meshes"])
        header["meshes"].append({
            "name": mesh.name,
            "positions": positions_view,
            "indices": indices_view
        })

    material_indices = {}
    for m in bpy.data.materials:
        # No support for shader node graphs/etc. just take the principled BSDF node
        principled_node = None
        for n in m.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                principled_node = n
                break
        if not principled_node:
            print("Error: Unsupported Material {}, no Principled BSDF found!".format(m.name))
            continue
        material_indices[m.name] = len(header["materials"])
        mat = {
            "name": m.name
        }
        for i in principled_node.inputs:
            if i.name == "Base Color":
                mat["base_color"] = [i.default_value[0], i.default_value[1], i.default_value[2]]
            elif i.name == "Metallic":
                mat["metallic"] = i.default_value
            elif i.name == "Specular":
                mat["specular"] = i.default_value
            elif i.name == "Specular Tint":
                mat["specular_tint"] = i.default_value
            elif i.name == "Roughness":
                mat["roughness"] = i.default_value
            elif i.name == "Anisotropic":
                mat["anisotropy"] = i.default_value
            elif i.name == "Sheen":
                mat["sheen"] = i.default_value
            elif i.name == "Sheen Tint":
                mat["sheen_tint"] = i.default_value
            elif i.name == "Clearcoat":
                mat["clearcoat"] = i.default_value
            elif i.name == "Clearcoat Roughness":
                mat["clearcoat_gloss"] = i.default_value
            elif i.name == "IOR":
                mat["ior"] = i.default_value
            elif i.name == "Transmission":
                # Note: we treat roughness as global instead of separating transmission
                # vs. reflection rougness
                mat["specular_transmission"] = i.default_value        
        header["materials"].append(mat)

    for o in scene.objects:
        # crts store the matrix as column-major and uses Y up
        tfm = (mathutils.Matrix.Rotation(math.radians(-90), 4, "X") @ o.matrix_world).transposed()
        obj_data = {
            "name": o.name,
            "type": o.type,
            "matrix": [
                tfm[0][0], tfm[0][1], tfm[0][2], tfm[0][3],
                tfm[1][0], tfm[1][1], tfm[1][2], tfm[1][3],
                tfm[2][0], tfm[2][1], tfm[2][2], tfm[2][3],
                tfm[3][0], tfm[3][1], tfm[3][2], tfm[3][3]
            ]
        }
        if o.type == "MESH":
            mat_id = -1
            if o.active_material:
                mat_id = material_indices[o.active_material.name]
            obj_data["material"] = mat_id
            obj_data["mesh"] = mesh_indices[o.data.name]
        elif o.type == "LIGHT":
            light = o.data
            if light.type != "AREA":
                print("Only area lights are supported")
            obj_data["color"] = [light.color.r, light.color.g, light.color.b]
            obj_data["energy"] = light.energy
            obj_data["size"] = [light.size, light.size_y]
        elif o.type == "CAMERA":
            cam = o.data
            obj_data["fov_y"] = math.degrees(cam.angle_y)
        else:
            print("Unhandled or ignored type {}".format(o.type))
            continue
        header["objects"].append(obj_data)

    with open(filepath, "wb") as f:
        # Pretty print the header for testing
        header_bytes = bytearray(json.dumps(header, indent=4), "utf-8")
        f.write(struct.pack("<Q", len(header_bytes)))
        f.write(header_bytes)
        for mesh in bpy.data.meshes:
            buffer = bytearray()
            write_mesh_vertex_buffer(mesh, buffer)
            write_mesh_index_buffer(mesh, buffer)
            f.write(buffer)               
    return { "FINISHED" }

class ExportCRTS(bpy.types.Operator, ExportHelper):
    """Save a ChameleonRT scene file"""

    bl_idname = "scene.crts"
    bl_label = "Export ChameleonRT"
    bl_options = { "PRESET" }
    filename_ext = ".crts"

    def execute(self, context):
        keywords = self.as_keywords(ignore=("check_existing", "filter_glob"))
        return export_crts(self, context, **keywords)

def menu_func(self, context):
    self.layout.operator(ExportCRTS.bl_idname, text="ChameleonRT scene (.crts)")

def register():
    bpy.utils.register_class(ExportCRTS)
    bpy.types.TOPBAR_MT_file_export.append(menu_func)

def unregister():
    bpy.utils.unregister_class(ExportCRTS)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func)

if __name__ == "__main__":
    register()
