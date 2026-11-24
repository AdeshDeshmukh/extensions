package goemitter

import "github.com/runtimeconditions/extensions/tooling/extension-bindings/normalizer"

// A package that adds fields to a dependency's kinds, such as env-configuration
// adding configuration to common-integrations' cache, has examples that call the
// dependency's declaration function with the dependency's interface types. The
// binding model already carries the dependency's vocabulary and schemas, and
// buildPackageIR keeps only what the root extension owns, so planning the same
// model with the dependency as root yields the dependency package's names.

// dependencyIR plans the package of a direct dependency extension. It returns
// false when the extension is not a direct dependency of this package or when
// its package cannot be planned from this model.
func (ir *packageIR) dependencyIR(extension string) (*packageIR, string, bool) {
	var dependency *PackageDependency
	for index := range ir.target.Dependencies {
		if ir.target.Dependencies[index].Extension == extension {
			dependency = &ir.target.Dependencies[index]
		}
	}
	if dependency == nil {
		return nil, "", false
	}
	if built, ok := ir.dependencyIRs[extension]; ok {
		return built, dependency.PackageName, built != nil
	}
	var built *packageIR
	if model, ok := dependencyModel(ir.model, extension); ok {
		target := PackageTarget{
			PackageKey: dependency.PackageName, RootExtension: extension,
			ModulePath: dependency.ModulePath, PackageName: dependency.PackageName, Version: dependency.Version,
		}
		if planned, err := buildPackageIR(model, target); err == nil {
			built = planned
		}
	}
	if ir.dependencyIRs == nil {
		ir.dependencyIRs = map[string]*packageIR{}
	}
	ir.dependencyIRs[extension] = built
	return built, dependency.PackageName, built != nil
}

// dependencyModel narrows a binding model to the closure of one of its
// extensions, with that extension as the root.
func dependencyModel(model normalizer.BindingModel, extension string) (normalizer.BindingModel, bool) {
	byID := map[string]normalizer.ResolvedExtension{}
	for _, item := range model.Extensions {
		byID[item.ID] = item
	}
	root, ok := byID[extension]
	if !ok {
		return normalizer.BindingModel{}, false
	}
	closure := map[string]bool{}
	var visit func(string)
	visit = func(id string) {
		item, found := byID[id]
		if !found || closure[id] {
			return
		}
		closure[id] = true
		for _, dependency := range item.Dependencies {
			visit(dependency)
		}
	}
	visit(extension)

	narrowed := model
	narrowed.RootExtension = normalizer.ArtifactIdentity{ID: root.ID, Version: root.Version, SemanticSHA256: root.SemanticSHA256}
	narrowed.Extensions = nil
	for _, item := range model.Extensions {
		if closure[item.ID] {
			narrowed.Extensions = append(narrowed.Extensions, item)
		}
	}
	vocabulary := normalizer.VocabularyModel{}
	for _, owner := range model.Vocabulary.Owners {
		if closure[owner.Owner] {
			vocabulary.Owners = append(vocabulary.Owners, owner)
		}
	}
	declarations := append(append([]normalizer.DeclarationModel(nil), model.Vocabulary.OwnedDeclarations...), model.Vocabulary.ImportedDeclarations...)
	for _, declaration := range declarations {
		switch {
		case declaration.Owner == extension:
			vocabulary.OwnedDeclarations = append(vocabulary.OwnedDeclarations, declaration)
		case closure[declaration.Owner]:
			vocabulary.ImportedDeclarations = append(vocabulary.ImportedDeclarations, declaration)
		}
	}
	for _, item := range model.Vocabulary.Interfaces {
		if closure[item.Owner] {
			vocabulary.Interfaces = append(vocabulary.Interfaces, item)
		}
	}
	for _, item := range model.Vocabulary.ConditionFields {
		if closure[item.Owner] {
			vocabulary.ConditionFields = append(vocabulary.ConditionFields, item)
		}
	}
	for _, item := range model.Vocabulary.InterfaceFields {
		if closure[item.Owner] {
			vocabulary.InterfaceFields = append(vocabulary.InterfaceFields, item)
		}
	}
	for _, item := range model.Vocabulary.ValueDomains {
		if closure[item.Owner] {
			vocabulary.ValueDomains = append(vocabulary.ValueDomains, item)
		}
	}
	narrowed.Vocabulary = vocabulary
	narrowed.Schemas = nil
	for _, schema := range model.Schemas {
		if closure[schema.Owner] {
			narrowed.Schemas = append(narrowed.Schemas, schema)
		}
	}
	return narrowed, true
}
