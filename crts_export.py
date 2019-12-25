import bpy
import mathutils
import math
import struct
import json
import os
import array
from bpy_extras.io_utils import ExportHelper

bl_info = {
    "name": "crts_export",
    "author": "Will Usher",
    "blender": (2, 80, 0),
    "version": (1, 1, 0),
    "location": "File > Import-Export",
    "description": "Export the scene to a ChameleonRT scene",
    "category": "Import-Export",
    "wiki_url": "https://github.com/Twinklebear/blender_export_crts"
}

def compute_mesh_buffer_sizes(mesh):
    # TODO: Also normals? It looks like there's some separate calc_normals_split method
    # but I'm not sure how that'll effect the indexing
    num_uvs = 0
    if mesh.uv_layers.active:
        # TODO: This assumes each vertex has only one UV, shared with all faces
        # sharing the same vertex position. Blender does not require this,
        # so maybe later can think about splitting vertices not sharing
        # the same UV coords
        num_uvs = len(mesh.vertices)
    return (len(mesh.vertices) * 3 * 4, len(mesh.loop_triangles) * 3 * 4, num_uvs * 2 * 4)

def write_mesh_info(meshes, header, byte_offset):
    mesh_indices = {}
    for mesh in meshes:
        mesh.calc_loop_triangles()
        verts_size, indices_size, uvs_size = compute_mesh_buffer_sizes(mesh)

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

        uvs_view = -1
        if uvs_size > 0:
            uvs_view = indices_view + 1
            header["buffer_views"].append({
                "byte_offset": byte_offset,
                "byte_length": uvs_size,
                "type": "VEC2_F32"
            })
            byte_offset += uvs_size

        mesh_indices[mesh.name] = len(header["meshes"])
        mesh_info = {
            "name": mesh.name,
            "positions": positions_view,
            "indices": indices_view
        }
        if uvs_view != -1:
            mesh_info["texcoords"] = uvs_view
        header["meshes"].append(mesh_info)
    return byte_offset, mesh_indices

def write_image_info(images, header, byte_offset):
    image_indices = {}
    for img in bpy.data.images:
        # TODO: For generated textures how could we bake them to images?
        # we could evaluate it to create a new image, pack that image into
        # the blend file temporarily to generate a PNG we can embed, then
        # delete the temp file when we're done exporting
        if img.source != "FILE":
            print("Skipping {}, non-file image sources are not supported".format(img.name))
            continue
        img_bytes = 0
        if img.packed_file:
            img_bytes = img.packed_file.size
        else:
            path = img.filepath_from_user()
            if not os.path.isfile(path):
                print("Image file {} is not packed and not on disk, skipping".format(img.name))
                continue
            img_bytes = os.path.getsize(path)
        view = len(header["buffer_views"])
        header["buffer_views"].append({
            "byte_offset": byte_offset,
            "byte_length": img_bytes,
            "type": "UINT_8"
        })
        byte_offset += img_bytes
        image_indices[img.name] = len(header["images"])
        header["images"].append({
            "name": img.name,
            "view": view,
            "type": img.file_format
        })
    return byte_offset, image_indices

def write_material_info(materials, header, image_indices):
    material_indices = {}
    for m in materials:
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
                if len(i.links) > 0:
                    texture = i.links[0].from_node
                    if texture.type != "TEX_IMAGE" or i.links[0].from_socket.type != "RGBA":
                        print("Unsupported input type/socket to base color {}/{}"
                                .format(texture.type, i.links[0].from_socket.type))
                        continue
                    if texture.image.name in image_indices:
                        mat["base_color_texture"] = image_indices[texture.image.name]
                    else:
                        print("Skipping assignment of base color texture {} for material {}"
                                .format(texture.image.name, m.name))
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
    return material_indices

def write_object_info(objects, header, material_indices, mesh_indices):
    for o in objects:
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


def write_mesh_buffers(mesh, output):
    for v in mesh.vertices:
        output.extend(struct.pack("<fff", v.co[0], v.co[1], v.co[2]))
    for t in mesh.loop_triangles:
        output.extend(struct.pack("<III", t.vertices[0], t.vertices[1], t.vertices[2]))
    if mesh.uv_layers.active:
        n_uvs = 0
        # TODO Maybe have support for different UVs on the same vertex position,
        # Blender supports this but in the exporter we'd have to duplicate
        # the vertex.
        uv_data = array.array("f", range(0, len(mesh.vertices) * 2))
        layer = mesh.uv_layers.active.data
        for p in mesh.polygons:
            for l in p.loop_indices:
                v = mesh.loops[l].vertex_index
                uv_data[v * 2] = layer[l].uv[0]
                uv_data[v * 2 + 1] = layer[l].uv[1]
        output.extend(uv_data)

def export_crts(operator, scene, filepath=""):
    header = {
        "meshes": [],
        "objects": [],
        "buffer_views": [],
        "materials": [],
        "images": []
    }

    byte_offset, mesh_indices = write_mesh_info(bpy.data.meshes, header, 0)
    byte_offset, image_indices = write_image_info(bpy.data.images, header, byte_offset)
    material_indices = write_material_info(bpy.data.materials, header, image_indices)
    write_object_info(scene.objects, header, material_indices, mesh_indices)

    with open(filepath, "wb") as f:
        # Pretty print the header for testing
        header_bytes = bytearray(json.dumps(header, indent=4), "utf-8")
        f.write(struct.pack("<Q", len(header_bytes)))
        f.write(header_bytes)
        # Write mesh buffers
        for mesh in bpy.data.meshes:
            buf = bytearray()
            write_mesh_buffers(mesh, buf)
            f.write(buf)               
        # Write image buffers
        for img in bpy.data.images:
            if img.packed_file:
                f.write(img.packed_file.data)
            else:
                path = img.filepath_from_user()
                if len(path) > 0 and os.path.isfile(path):
                    with open(path, "rb") as fimg:
                        f.write(fimg.read())
    return {"FINISHED"}

class ExportCRTS(bpy.types.Operator, ExportHelper):
    """Save a ChameleonRT scene file"""

    bl_idname = "scene.crts"
    bl_label = "Export ChameleonRT"
    bl_options = { "PRESET" }
    filename_ext = ".crts"

    def execute(self, context):
        keywords = self.as_keywords(ignore=("check_existing", "filter_glob"))
        return export_crts(self, context.scene, **keywords)

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
