import os

import github.GithubException
import requests
from django.db import models
from django.utils.translation import gettext_lazy as _
from github import Github

from fregepoc.indexers.base import BaseIndexer
from fregepoc.indexers.sourceforge import (
    SinglePageProjectsExtractor,
    SingleProjectCodeUrlExtractor,
    SingleProjectGitLinkExtractor,
    SingleProjectGitUrlExtractor,
    SingleProjectResponseExtractor,
)
from fregepoc.indexers.utils import bitbucket
from fregepoc.repositories.models import Repository


class GitHubIndexer(BaseIndexer):
    min_forks = models.PositiveIntegerField(
        _("min forks"),
        default=100,
    )
    min_stars = models.PositiveIntegerField(
        _("min stars"),
        default=100,
    )
    current_page = models.PositiveIntegerField(
        _("current page"),
        default=0,
        help_text=_("The last visited page via GitHub API."),
    )

    def __iter__(self):
        github_token = os.environ.get("GITHUB_TOKEN")
        g = Github(github_token) if github_token else Github()
        while True:
            try:
                list_of_repos = g.search_repositories(
                    query=(
                        f"forks:>={self.min_forks} "
                        f"stars:>={self.min_stars} is:public"
                    ),
                    sort="stars",
                    page=self.current_page,
                )
                self.current_page += 1
                self.save(update_fields=["current_page"])
                repos_to_process = [
                    Repository(
                        name=repo.name,
                        description=repo.description,
                        git_url=repo.clone_url,
                        repo_url=repo.html_url,
                        commit_hash=repo.get_branch(
                            repo.default_branch
                        ).commit.sha,
                    )
                    for repo in list_of_repos
                ]
                Repository.objects.bulk_create(repos_to_process)
            except github.RateLimitExceededException:
                self.rate_limit_exceeded = True
                break
            yield repos_to_process

    class Meta:
        verbose_name = _("Github Indexer")
        verbose_name_plural = verbose_name


class SourceforgeIndexer(BaseIndexer):
    current_page = models.PositiveIntegerField(
        _("current page"),
        default=1,
        help_text=_("The last visited page."),
    )

    singlePageProjectsExtractor = SinglePageProjectsExtractor()
    singleProjectCodeUrlExtractor = SingleProjectCodeUrlExtractor()
    singleProjectGitLinkExtractor = SingleProjectGitLinkExtractor()
    singleProjectResponseExtractor = SingleProjectResponseExtractor()
    singleProjectGitUrlExtractor = SingleProjectGitUrlExtractor()

    def __iter__(self):
        while True:
            try:
                yield self.main_loop()
            except requests.exceptions.RequestException:
                pass

    def main_loop(self):
        projects = self.singlePageProjectsExtractor.extract(self.current_page)

        self.current_page += 1
        if self.current_page >= 1000:
            # The maximum page on the SourceForge is 999. When we reach the
            # limit, we just start over. In the future, we may want to change
            # by using some filters (categories) to scrap more data.
            self.current_page = 0
        self.save(update_fields=["current_page"])

        if not projects:
            return []

        for project_name in projects:
            repos_to_process = []

            single_project_soup = self.singleProjectResponseExtractor.extract(
                project_name
            )

            project_code_url = self.singleProjectCodeUrlExtractor.extract(
                single_project_soup
            )
            repo_from_code_url = self.handle_code_url(
                project_name, project_code_url
            )
            if repo_from_code_url is not None:
                repos_to_process.append(repo_from_code_url)

            project_git_url = self.singleProjectGitUrlExtractor.extract(
                single_project_soup
            )
            repos_to_process.extend(
                self.handle_git_url(project_name, project_git_url)
            )

            Repository.objects.bulk_create(repos_to_process)
            return repos_to_process

    def handle_code_url(self, project_name, project_code_url):
        if project_code_url is None:
            return None

        project_git_url_from_code_url = (
            self.singleProjectGitLinkExtractor.extract(project_code_url)
        )
        if project_git_url_from_code_url is None:
            return None

        return Repository(
            name=project_name,
            description=project_name,
            git_url=project_git_url_from_code_url,
            repo_url=project_code_url,
            commit_hash="HEAD",  # TODO
        )

    def handle_git_url(self, project_name, project_git_urls):
        extracted_git_url = [
            (subproject, self.singleProjectGitLinkExtractor.extract(git_url))
            for (subproject, git_url) in project_git_urls
        ]

        return [
            Repository(
                name=f"{project_name}/{subproject}",
                description=project_name,
                git_url=git_url,
                repo_url=git_url,  # TODO
                commit_hash="HEAD",  # TODO
            )
            for (subproject, git_url) in extracted_git_url
            if git_url is not None
        ]

    class Meta:
        verbose_name = _("Sourceforge Indexer")
        verbose_name_plural = verbose_name


class BitbucketIndexer(BaseIndexer):
    min_forks = models.PositiveIntegerField(
        _("min forks"),
        default=1,
    )

    current_date = models.DateTimeField(
        _("current date"),
        default=bitbucket.DEFAULT_DATE,
        help_text=_(
            "The creation date of repository from which to start searching. "
            "Please note that Bitbucket API paginates repos by creation date, "
            "so the dates are used to iterate over repositories."
        ),
    )

    def __iter__(self):
        while True:
            repository_data, next_date = bitbucket.get_next_page(
                self.current_date
            )

            if not repository_data:
                self.rate_limit_exceeded = True
                break

            self.current_date = next_date
            self.save(update_fields=["current_date"])

            if (
                self.min_forks
                and bitbucket.get_forks_count(repository_data) < self.min_forks
            ):
                continue

            clone_url = bitbucket.get_clone_url(repository_data)
            repo_url = bitbucket.get_repo_url(repository_data)
            commit_hash = bitbucket.get_last_commit_hash(repository_data)

            if not (clone_url and repo_url and commit_hash):
                continue

            repo_to_process = Repository.objects.create(
                name=repository_data.get("name"),
                description=repository_data.get("description"),
                git_url=clone_url,
                repo_url=repo_url,
                commit_hash=commit_hash,
            )

            yield repo_to_process

    class Meta:
        verbose_name = _("Bitbucket Indexer")
        verbose_name_plural = verbose_name
