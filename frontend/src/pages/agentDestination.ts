export function resolveProjectId(projectId: string, projects: Array<{ id: string }>) {
  return projects.some(project => project.id === projectId) ? projectId : projects[0]?.id || ''
}
