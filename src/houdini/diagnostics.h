#pragma once
#include "pxr/pxr.h"
#include <memory>
PXR_NAMESPACE_OPEN_SCOPE
struct HdUEState;
class HdUERenderDelegate;
void HdUERegisterViewport(const std::shared_ptr<HdUEState>& state, HdUERenderDelegate* delegate);
void HdUEUnregisterViewport(HdUERenderDelegate* delegate);
PXR_NAMESPACE_CLOSE_SCOPE
