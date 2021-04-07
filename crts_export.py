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
    "version": (1, 4, 0),
    "location": "File > Import-Export",
    "description": "Export the scene to a ChameleonRT scene",
    "category": "Import-Export",
    "wiki_url": "https://github.com/Twinklebear/blender_export_crts"
}

def compute_mesh_buffer_sizes(mesh):
    unique_verts = {}
    uvs = {}
    normals = {}
    for t in mesh.loop_triangles:
        for l in t.loops:
            vert_idx = mesh.loops[l].vertex_index
            uv_idx = -1
            if len(mesh.uv_layers) > 0:
                uv = (mesh.uv_layers.active.data[l].uv[0], mesh.uv_layers.active.data[l].uv[1])
                if uv not in uvs:
                    uvs[uv] = l
                    uv_idx = l
                else:
                    uv_idx = uvs[uv]
            
            normal = (mesh.loops[l].normal[0], mesh.loops[l].normal[1], mesh.loops[l].normal[2])
            n_idx = -1
            if normal not in normals:
                normals[normal] = l
                n_idx = l
            else:
                n_idx = normals[normal]

            idx = (vert_idx, uv_idx, n_idx)
            if not idx in unique_verts:
                vid = len(unique_verts)
                unique_verts[idx] = vid
    n_verts = len(unique_verts)
    uvs_size = 0
    if len(uvs) > 0:
        uvs_size = n_verts * 2 * 4
    return (n_verts * 3 * 4, len(mesh.loop_triangles) * 3 * 4, uvs_size, n_verts * 3 * 4)

def write_mesh_info(meshes, header, byte_offset):
    mesh_indices = {}
    for mesh in meshes:
        if mesh.users == 0:
            continue

        mesh.calc_loop_triangles()
        mesh.calc_normals_split()
        verts_size, indices_size, uvs_size, normals_size = compute_mesh_buffer_sizes(mesh)

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
            
        normals_view = -1
        if normals_size > 0:
            normals_view = uvs_view + 1
            header["buffer_views"].append({
                "byte_offset": byte_offset,
                "byte_length": normals_size,
                "type": "VEC3_F32"
            })
            byte_offset += normals_size

        mesh_indices[mesh.name] = len(header["meshes"])
        mesh_info = {
            "name": mesh.name,
            "positions": positions_view,
            "indices": indices_view
        }
        if uvs_view != -1:
            mesh_info["texcoords"] = uvs_view
        if normals_view != -1:
            mesh_info["normals"] = normals_view
        header["meshes"].append(mesh_info)
    return byte_offset, mesh_indices

def write_image_info(images, header, byte_offset):
    image_indices = {}
    for img in bpy.data.images:
        if img.users == 0:
            continue
        
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
        color_space = "sRGB"
        if img.colorspace_settings.name != "sRGB":
            color_space = "LINEAR"
        header["images"].append({
            "name": img.name,
            "view": view,
            "type": img.file_format,
            "color_space": color_space            
        })
    return byte_offset, image_indices

def get_seprgb_texture_info(link):
    from_node = link.from_node
    if from_node.type != "SEPRGB":
        print("Only Separate RGB nodes may be input to scalar material property, found {}"
                .format(from_node.type))
        return None
    else:
        channel = link.from_socket.name
        if channel == "R":
            channel = 0
        elif channel == "G":
            channel = 1
        else:
            channel = 2
        return (from_node.inputs["Image"].links[0].from_node.image, channel)

def write_material_info(materials, header, image_indices):
    material_indices = {}
    for m in materials:
        if m.users == 0:
            continue
        
        # No support for shader node graphs/etc. just take the principled BSDF node
        principled_node = None
        if m.node_tree:
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
        export_param_list = ["Metallic", "Specular", "Specular Tint", "Roughness",
            "Anisotropic", "Sheen", "Sheen Tint", "Clearcoat", "Clearcoat Roughness",
            "IOR", "Transmission"]
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
            elif i.name in export_param_list:
                json_name = i.name.lower().replace(" ", "_")
                mat[json_name] = i.default_value
                if len(i.links) > 0:
                    tex_info = get_seprgb_texture_info(i.links[0])
                    if tex_info:
                        mat[json_name + "_texture"] = {
                            "texture": image_indices[tex_info[0].name],
                            "channel": tex_info[1]
                        }
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
            obj_data["energy"] = light.energy / (light.size * light.size_y)
            obj_data["size"] = [light.size, light.size_y]
        elif o.type == "CAMERA":
            cam = o.data
            obj_data["fov_y"] = math.degrees(cam.angle_y)
        else:
            print("Unhandled or ignored type {}".format(o.type))
            continue
        header["objects"].append(obj_data)


def write_mesh_buffers(mesh, output):
    unique_verts = {}
    vertex_list = []
    vertex_indices = []
    uvs = {}
    normals = {}
    for t in mesh.loop_triangles:
        for l in t.loops:
            vert_idx = mesh.loops[l].vertex_index

            uv_idx = -1
            if len(mesh.uv_layers) > 0:
                uv = (mesh.uv_layers.active.data[l].uv[0], mesh.uv_layers.active.data[l].uv[1])
                if uv not in uvs:
                    uvs[uv] = l
                    uv_idx = l
                else:
                    uv_idx = uvs[uv]
                
            normal = (mesh.loops[l].normal[0], mesh.loops[l].normal[1], mesh.loops[l].normal[2])
            n_idx = -1
            if normal not in normals:
                normals[normal] = l
                n_idx = l
            else:
                n_idx = normals[normal]

            idx = (vert_idx, uv_idx, n_idx)
            vid = -1
            if not idx in unique_verts:
                vid = len(vertex_list)
                unique_verts[idx] = vid
                vertex_list.append(idx)
            else:
                vid = unique_verts[idx]
            vertex_indices.append(vid)
            
    for v in vertex_list:
        vert = mesh.vertices[v[0]]
        output.extend(struct.pack("<fff", vert.co[0], vert.co[1], vert.co[2]))
    
    for id in vertex_indices:
        output.extend(struct.pack("<I", id))

    if len(mesh.uv_layers) > 0:
        for v in vertex_list:
            uv = [mesh.uv_layers.active.data[v[1]].uv[0], mesh.uv_layers.active.data[v[1]].uv[1]]
            output.extend(struct.pack("<ff", uv[0], uv[1]))

    for v in vertex_list:
        normal = [mesh.loops[v[2]].normal[0], mesh.loops[v[2]].normal[1], mesh.loops[v[2]].normal[2]]
        output.extend(struct.pack("<fff", normal[0], normal[1], normal[2]))

def export_crts(operator, scene, filepath=""):
    header = {
        "meshes": [],
        "objects": [],
        "buffer_views": [],
        "materials": [],
        "images": []
    }

    # Check no meshes are using multiple materials
    for mesh in bpy.data.meshes:
        if mesh.users == 0:
            continue
        if len(mesh.materials) > 1:
            operator.report({"ERROR"},
                    "Please split mesh '{}' into per-material meshes to ensure proper export".format(mesh.name))
            return {"CANCELLED"}

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
            if mesh.users == 0:
                continue
            
            buf = bytearray()
            write_mesh_buffers(mesh, buf)
            f.write(buf)               
        # Write image buffers
        for img in bpy.data.images:
            if img.users == 0:
                continue
            
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
