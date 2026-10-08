"""Runs INSIDE Blender (blender -b --python blender_script.py -- jobs.json). The maps arrive resolved by
targets/shading.py: albedo, normal (green up), roughness, metallic, emissive."""
import json
import os
import sys

import bpy


def _input(node, *names):
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    return None


def img(nt, path, noncolor, x, y):
    n = nt.nodes.new("ShaderNodeTexImage")
    n.image = bpy.data.images.load(path, check_existing=True)
    n.location = (x, y)
    if noncolor:
        n.image.colorspace_settings.name = "Non-Color"
    return n


def build_material(name, spec):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (700, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (400, 0)
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    roles, flags = spec["roles"], set(spec.get("flags", []))
    if spec.get("drop"):                 # opaque decal layer: it would paint a solid patch over the surface
        _input(bsdf, "Alpha").default_value = 0.0
        mat.surface_render_method = "BLENDED" if hasattr(mat, "surface_render_method") else None
        return mat

    if "base" in roles:
        t = img(nt, roles["base"], False, -500, 300)
        nt.links.new(t.outputs["Color"], _input(bsdf, "Base Color"))
        if "alpha_test" in flags or "translucent" in flags:
            nt.links.new(t.outputs["Alpha"], _input(bsdf, "Alpha"))
            mat.surface_render_method = "BLENDED" if hasattr(mat, "surface_render_method") else None
    for role, socket in (("rough", "Roughness"), ("metal", "Metallic")):
        if role in roles:
            t = img(nt, roles[role], True, -700, 150 if role == "rough" else -50)
            nt.links.new(t.outputs["Color"], _input(bsdf, socket))
    if "normal" in roles:
        t = img(nt, roles["normal"], True, -500, -300)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location = (-100, -300)
        nt.links.new(t.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], _input(bsdf, "Normal"))
    if "emissive" in roles:
        t = img(nt, roles["emissive"], False, -500, -650)
        e = _input(bsdf, "Emission Color", "Emission")
        if e is not None:
            nt.links.new(t.outputs["Color"], e)
        es = _input(bsdf, "Emission Strength")
        if es is not None:
            es.default_value = 1.0
    return mat


def main():
    jobs = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
    for job in jobs:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=job["glb"])
        for m in bpy.data.materials:                      # free the names for our own materials
            m.name = "_imported_" + m.name
        built = {n: build_material(n, s) for n, s in job["materials"].items()}
        for ob in bpy.context.scene.objects:
            if ob.type != "MESH":
                continue
            for i, slot in enumerate(ob.material_slots):
                nm = slot.material.name if slot.material else None
                base = nm[len("_imported_"):].rsplit(".", 1)[0] if nm and nm.startswith("_imported_") else None
                if base in built:
                    slot.material = built[base]
            ob.name = job["name"].split("/")[-1]
        for m in list(bpy.data.materials):
            if m.users == 0:
                bpy.data.materials.remove(m)
        os.makedirs(os.path.dirname(job["blend"]), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=job["blend"], relative_remap=True)
        print("BLEND", job["blend"])


main()
