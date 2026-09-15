#![forbid(unsafe_code)]

use a13n::generated::{
    apis::{
        Response, agent_management_api, configuration::Configuration, environments_api,
        protocol_gateway_api, skill_management_api,
    },
    models::LabelsBody,
};
use clap::{Parser, Subcommand, ValueEnum};
use std::{collections::BTreeMap, env};

#[derive(Debug, Parser)]
#[command(
    name = "a13n-service-cli",
    version,
    about = "Command-line client for a13n Service"
)]
struct Cli {
    /// Service API base URL.
    #[arg(long, default_value = "http://localhost")]
    base_url: String,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Read or atomically replace resource labels.
    Labels {
        #[arg(value_enum)]
        resource: Resource,
        id: String,
        /// Workspace ID or key, required for Agent resources.
        #[arg(long)]
        workspace: Option<String>,
        /// Complete JSON string-to-string map. Omit to read labels.
        #[arg(long, requires = "if_match")]
        set: Option<String>,
        /// Exact label ETag required with --set.
        #[arg(long, requires = "set")]
        if_match: Option<String>,
    },
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum Resource {
    Agent,
    Session,
    Thread,
    Run,
    Skill,
    EnvironmentTemplate,
    Environment,
}

async fn run(cli: Cli) -> Result<(), String> {
    let Command::Labels {
        resource,
        id,
        workspace,
        set,
        if_match,
    } = cli.command;
    let workspace = match (resource, workspace.as_deref()) {
        (Resource::Agent, None) => {
            return Err("--workspace is required for agent labels".to_owned());
        }
        (_, value) => value.unwrap_or(""),
    };
    let replacement = set
        .map(|json| {
            let labels: BTreeMap<String, String> = serde_json::from_str(&json)
                .map_err(|error| format!("--set must be a JSON string-to-string map: {error}"))?;
            Ok::<_, String>(LabelsBody::new(
                serde_json::to_value(labels).map_err(|error| error.to_string())?,
            ))
        })
        .transpose()?;
    let token = env::var("A13N_TOKEN").map_err(|_| "A13N_TOKEN must be set".to_owned())?;
    let mut configuration = Configuration::new();
    configuration.base_path = cli.base_url.trim_end_matches('/').to_owned();
    configuration.bearer_access_token = Some(token);
    let response = match replacement {
        Some(body) => {
            let etag = if_match
                .as_deref()
                .ok_or("--if-match is required with --set")?;
            replace_labels(&configuration, resource, &id, workspace, etag, body).await?
        }
        None => read_labels(&configuration, resource, &id, workspace).await?,
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&response.data).map_err(|error| error.to_string())?
    );
    if let Some(etag) = response
        .headers
        .get("etag")
        .and_then(|value| value.to_str().ok())
    {
        eprintln!("ETag: {etag}");
    }
    Ok(())
}

async fn read_labels(
    configuration: &Configuration,
    resource: Resource,
    id: &str,
    workspace: &str,
) -> Result<Response<LabelsBody>, String> {
    match resource {
        Resource::Agent => agent_management_api::get_workspaces_workspace_agents_agent_labels(
            configuration,
            workspace,
            id,
        )
        .await
        .map_err(|error| error.to_string()),
        Resource::Session => {
            protocol_gateway_api::get_sessions_session_id_labels(configuration, id)
                .await
                .map_err(|error| error.to_string())
        }
        Resource::Thread => protocol_gateway_api::get_threads_thread_id_labels(configuration, id)
            .await
            .map_err(|error| error.to_string()),
        Resource::Run => protocol_gateway_api::get_runs_run_id_labels(configuration, id)
            .await
            .map_err(|error| error.to_string()),
        Resource::Skill => skill_management_api::get_skills_skill_id_labels(configuration, id)
            .await
            .map_err(|error| error.to_string()),
        Resource::EnvironmentTemplate => {
            environments_api::get_environment_templates_template_id_labels(configuration, id)
                .await
                .map_err(|error| error.to_string())
        }
        Resource::Environment => {
            environments_api::get_environments_environment_id_labels(configuration, id)
                .await
                .map_err(|error| error.to_string())
        }
    }
}

async fn replace_labels(
    configuration: &Configuration,
    resource: Resource,
    id: &str,
    workspace: &str,
    etag: &str,
    body: LabelsBody,
) -> Result<Response<LabelsBody>, String> {
    match resource {
        Resource::Agent => agent_management_api::put_workspaces_workspace_agents_agent_labels(
            configuration,
            workspace,
            id,
            etag,
            body,
        )
        .await
        .map_err(|error| error.to_string()),
        Resource::Session => {
            protocol_gateway_api::put_sessions_session_id_labels(configuration, id, etag, body)
                .await
                .map_err(|error| error.to_string())
        }
        Resource::Thread => {
            protocol_gateway_api::put_threads_thread_id_labels(configuration, id, etag, body)
                .await
                .map_err(|error| error.to_string())
        }
        Resource::Run => {
            protocol_gateway_api::put_runs_run_id_labels(configuration, id, etag, body)
                .await
                .map_err(|error| error.to_string())
        }
        Resource::Skill => {
            skill_management_api::put_skills_skill_id_labels(configuration, id, etag, body)
                .await
                .map_err(|error| error.to_string())
        }
        Resource::EnvironmentTemplate => {
            environments_api::put_environment_templates_template_id_labels(
                configuration,
                id,
                etag,
                body,
            )
            .await
            .map_err(|error| error.to_string())
        }
        Resource::Environment => {
            environments_api::put_environments_environment_id_labels(configuration, id, etag, body)
                .await
                .map_err(|error| error.to_string())
        }
    }
}

#[tokio::main]
async fn main() {
    if let Err(error) = run(Cli::parse()).await {
        eprintln!("error: {error}");
        std::process::exit(1);
    }
}
