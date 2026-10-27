#pragma once
#include "delegate.h"
PXR_NAMESPACE_OPEN_SCOPE
inline VtDictionary HdUEWetSettings(const VtDictionary& bindings, const HdUEMeshData& mesh) {
    auto it = bindings.find(mesh.bindingPath.empty() ? mesh.scenePath : mesh.bindingPath);
    return it != bindings.end() && it->second.IsHolding<VtDictionary>() ? it->second.UncheckedGet<VtDictionary>() : VtDictionary();
}
template<class T> inline T HdUEWetValue(const VtDictionary& settings, const char* name, T fallback) {
    const auto it = settings.find(name);
    return it != settings.end() && it->second.IsHolding<T>() ? it->second.UncheckedGet<T>() : fallback;
}
PXR_NAMESPACE_CLOSE_SCOPE
