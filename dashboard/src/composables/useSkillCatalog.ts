import { ref, type Ref } from 'vue'
import { type CatalogSkill, loadCatalog } from '../utils/skillCatalog'

/** The catalog's skills. Empty until it loads. */
export function useSkillCatalog(): Ref<CatalogSkill[]> {
  const skills = ref<CatalogSkill[]>([])
  void loadCatalog().then((loaded) => {
    skills.value = loaded
  })
  return skills
}
