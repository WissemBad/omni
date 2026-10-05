"""Runs INSIDE Blender (blender -b --python blender_script.py -- jobs.json)."""
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
    roles, params, flags = spec["roles"], spec["params"], set(spec.get("flags", []))

    if "base" in roles:
        t = img(nt, roles["base"], False, -500, 300)
        nt.links.new(t.outputs["Color"], _input(bsdf, "Base Color"))
        if "alpha_test" in flags or "translucent" in flags:
            nt.links.new(t.outputs["Alpha"], _input(bsdf, "Alpha"))
            mat.surface_render_method = "BLENDED" if hasattr(mat, "surface_render_method") else None
    srm = roles.get("srm")
    if srm:
        t = img(nt, srm, True, -700, 0)
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = (-420, 0)
        nt.links.new(t.outputs["Color"], sep.inputs[0])
        rmin = (params.get("Roughness_Min") or [0.0])[0]
        rmax = (params.get("Roughness_Max") or [1.0])[0]
        mr = nt.nodes.new("ShaderNodeMapRange")
        mr.location = (-200, 0)
        mr.inputs["To Min"].default_value = rmin
        mr.inputs["To Max"].default_value = rmax
        nt.links.new(sep.outputs[1], mr.inputs[0])
        nt.links.new(mr.outputs[0], _input(bsdf, "Roughness"))
        s = _input(bsdf, "Specular IOR Level", "Specular")
        if s is not None:
            nt.links.new(sep.outputs[0], s)
        nt.links.new(sep.outputs[2], _input(bsdf, "Metallic"))
    elif "spec" in roles:
        t = img(nt, roles["spec"], True, -700, 0)
        bw = nt.nodes.new("ShaderNodeRGBToBW")
        bw.location = (-420, 0)
        nt.links.new(t.outputs["Color"], bw.inputs[0])
        s = _input(bsdf, "Specular IOR Level", "Specular")
        if s is not None:
            nt.links.new(bw.outputs[0], s)
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
